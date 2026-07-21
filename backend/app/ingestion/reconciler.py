"""Source-agnostic reconcile engine for the public regulation corpus.

For a source's yielded docs the engine computes a content hash, diffs it against
the DB index, and applies:

    NEW        (source_id unseen)      → chunk + embed + insert
    CHANGED    (hash differs)          → re-chunk + re-embed + replace that doc's chunks
    UNCHANGED  (hash matches)          → touch last_seen_at only (NO re-embed)
    ABSENT     (in DB, not seen)       → purge per the source's PurgePolicy

Safety guards (never let a bad fetch or an outage shrink/empty the corpus):

  1. Systemic-fetch guard  — if the adapter pull raised, yielded 0, or fell far
     below the expected doc count, ABORT before any delete (DELETE-policy sources).
  2. Systemic-embed guard  — (reused from #150) embed BEFORE any delete; if the
     embedding gateway is down (zero/below-floor embedded), ABORT without deleting.
  3. Mass-purge breaker    — if would-purge / existing > 30%, ABORT the whole run
     (a source reshaping its format can make everything look "absent").

All chunk mutations run in ONE transaction committed once per run, so a mid-run
failure rolls back — never a partial or empty corpus. A ``reg_ingestion_runs``
row (counts + terminal status) is written for EVERY run, including aborts.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.types import Adapter, IngestionMode, PurgePolicy, SourceDoc
from app.models.reg_ingestion_run import RegIngestionRun
from app.models.regulation_chunk import RegulationChunk
from app.services import knowledge_service as ks

logger = logging.getLogger(__name__)

# Mass-purge circuit-breaker: if a single reconcile run would purge more than this
# fraction of a source's existing docs, treat it as a source-format break (not a
# real mass removal) and abort the whole run, preserving the corpus. 0.30 mirrors
# the spec's §5 threshold.
_MASS_PURGE_MAX_FRACTION = 0.30


class SystemicFetchError(RuntimeError):
    """The adapter pull failed or looked systemically incomplete (raised, 0 docs,
    or far below the expected count). The corpus is left untouched — a bad fetch
    must NEVER drive a purge."""


class MassPurgeError(RuntimeError):
    """A reconcile run would purge more than the allowed fraction of a source's
    docs — almost certainly a source-format change making everything look absent.
    The whole run is rolled back and the corpus preserved."""


@dataclass
class RunSummary:
    """Terminal result of one reconcile run (mirrors the ``reg_ingestion_runs``
    row plus the inserted-chunk-row count for the admin trigger's response)."""

    run_id: uuid.UUID
    source: str
    mode: str
    status: str
    docs_seen: int = 0
    docs_new: int = 0
    docs_changed: int = 0
    docs_unchanged: int = 0
    docs_purged: int = 0
    embed_skipped: int = 0
    rows_inserted: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return self.status == "success"


def _content_hash(text_content: str) -> str:
    """Stable hash of a doc's text for change detection."""
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


async def _load_index(db: AsyncSession, source_corpus: str) -> dict[str, str]:
    """Load ``{source_id: content_hash}`` for a source's currently-tracked chunks.

    Rows with a NULL ``source_id`` (ingested before reconcile tracking existed)
    are excluded — they are handled by the one-time legacy sweep on a full
    reconcile so the first post-migration run does not duplicate them.
    """
    res = await db.execute(
        select(RegulationChunk.source_id, RegulationChunk.content_hash).where(
            RegulationChunk.source_corpus == source_corpus,
            RegulationChunk.source_id.is_not(None),
        )
    )
    index: dict[str, str] = {}
    for source_id, content_hash in res.all():
        # All sub-chunks of a doc share its hash; last write wins (identical).
        index[source_id] = content_hash or ""
    return index


def _guard_fetch(adapter: Adapter, docs: list[SourceDoc]) -> str | None:
    """Return a failure reason if the pull looks systemically bad, else None.

    Only DELETE-policy sources are guarded on emptiness/undercount: for them a
    bad fetch would drive a catastrophic purge. For a RETAIN (append-only) source
    a 0-doc pull is legitimate (a quiet week) and purges nothing, so it is allowed.
    """
    if adapter.purge_policy is not PurgePolicy.DELETE:
        return None
    if not docs:
        return "fetch yielded 0 documents for a purge-on-absence source"
    if adapter.min_expected_docs and len(docs) < adapter.min_expected_docs:
        return (
            f"fetch yielded {len(docs)} documents, far below the expected floor "
            f"of {adapter.min_expected_docs} — treating as a broken fetch"
        )
    return None


def _insert_row_sql(has_vector: bool) -> str:
    columns = [
        "id", "jurisdiction", "source_corpus", "source_url", "citation",
        "title", "part", "section", "program", "text_content", "token_count",
        "effective_date", "source_id", "content_hash", "last_seen_at",
        "ingestion_run_id",
    ]
    if has_vector:
        columns.append("embedding")
    placeholders = ", ".join(f":{c}" for c in columns)
    return (
        f"INSERT INTO disability_reg_chunks ({', '.join(columns)}) "
        f"VALUES ({placeholders})"
    )


async def _insert_chunk_row(
    db: AsyncSession,
    *,
    doc: SourceDoc,
    sub_text: str,
    content_hash: str,
    run_id: uuid.UUID,
    embedding: list[float] | None,
    has_vector: bool,
    now: datetime,
) -> None:
    meta = doc.metadata
    params = {
        "id": str(uuid.uuid4()),
        "jurisdiction": meta.get("jurisdiction", "US_Federal"),
        "source_corpus": meta["source_corpus"],
        "source_url": meta["source_url"],
        "citation": meta["citation"],
        "title": meta.get("title"),
        "part": meta.get("part"),
        "section": meta.get("section"),
        "program": meta.get("program", "Both"),
        "text_content": sub_text,
        "token_count": len(sub_text) // 4,
        "effective_date": meta.get("effective_date") or now,
        "source_id": doc.source_id,
        "content_hash": content_hash,
        "last_seen_at": now,
        "ingestion_run_id": str(run_id),
    }
    if has_vector:
        params["embedding"] = str(embedding)
    await db.execute(text(_insert_row_sql(has_vector)), params)


async def _record_run(db: AsyncSession, summary: RunSummary) -> None:
    """Persist the run row and commit. Called after a rollback on abort paths, so
    the audit row survives even when the chunk mutations were discarded."""
    db.add(
        RegIngestionRun(
            id=summary.run_id,
            source=summary.source,
            mode=summary.mode,
            started_at=summary.started_at,
            finished_at=datetime.now(UTC),
            status=summary.status,
            docs_seen=summary.docs_seen,
            docs_new=summary.docs_new,
            docs_changed=summary.docs_changed,
            docs_unchanged=summary.docs_unchanged,
            docs_purged=summary.docs_purged,
            embed_skipped=summary.embed_skipped,
            error=summary.error,
        )
    )
    await db.commit()


async def _retention_sweep(
    db: AsyncSession, source_corpus: str, retention_months: int, now: datetime
) -> int:
    """Delete docs whose effective_date is older than the rolling window.

    Returns the number of DISTINCT docs (source_ids) removed. Applies only to
    RETAIN sources on a full reconcile — this is intentional aging-out, NOT
    absence-purge, so the mass-purge breaker does not gate it.
    """
    cutoff = now - timedelta(days=retention_months * 30)
    doomed = await db.execute(
        select(func.count(func.distinct(RegulationChunk.source_id))).where(
            RegulationChunk.source_corpus == source_corpus,
            RegulationChunk.effective_date < cutoff,
        )
    )
    removed = doomed.scalar() or 0
    if removed:
        await db.execute(
            delete(RegulationChunk).where(
                RegulationChunk.source_corpus == source_corpus,
                RegulationChunk.effective_date < cutoff,
            )
        )
        logger.info(
            "Retention sweep removed %d %s doc(s) older than %d months (cutoff %s)",
            removed, source_corpus, retention_months, cutoff.date(),
        )
    return removed


async def _purge_legacy_untracked(
    db: AsyncSession, source_corpus: str, run_id: uuid.UUID
) -> int:
    """Remove pre-reconcile rows (NULL ``source_id``) for a source after a healthy
    full reconcile has repopulated it with tracked rows.

    Without this, the FIRST reconcile after the 047 migration would DOUBLE the
    corpus (legacy untracked rows + new tracked rows with the same citations).
    Safe: it only runs in RECONCILE mode, only after the new tracked rows are
    already inserted in this same transaction (guards passed), and only touches
    rows that predate reconcile tracking. Idempotent — a no-op on later runs.
    """
    res = await db.execute(
        delete(RegulationChunk).where(
            RegulationChunk.source_corpus == source_corpus,
            RegulationChunk.source_id.is_(None),
            # Never delete a row this very run just wrote.
            RegulationChunk.ingestion_run_id.is_(None),
        )
    )
    removed = res.rowcount or 0
    if removed:
        logger.info(
            "Purged %d legacy untracked %s chunk row(s) superseded by reconcile run %s",
            removed, source_corpus, run_id,
        )
    return removed


async def run_source(
    db: AsyncSession, adapter: Adapter, mode: IngestionMode
) -> RunSummary:
    """Reconcile one source. Returns a :class:`RunSummary`; NEVER leaves the corpus
    partial or empty. Every run records a ``reg_ingestion_runs`` row.

    The passed ``db`` session is owned by this call — it commits on success and
    rolls back before recording the run on any guarded abort.
    """
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    summary = RunSummary(
        run_id=run_id,
        source=adapter.source_corpus,
        mode=mode.value,
        status="running",
        started_at=now,
    )
    has_vector = await ks.has_pgvector(db)

    # 1. Fetch BEFORE touching the corpus. A raised fetch is systemic → abort.
    try:
        docs = list(await adapter.list_documents(mode))
    except Exception as exc:  # noqa: BLE001 — recorded + surfaced as an abort
        logger.exception("Adapter fetch failed for %s", adapter.source_corpus)
        summary.status = "aborted_fetch"
        summary.error = f"fetch failed: {exc}"
        await db.rollback()
        await _record_run(db, summary)
        return summary

    summary.docs_seen = len(docs)

    # 2. Systemic-fetch guard — never purge on a bad fetch.
    fetch_problem = _guard_fetch(adapter, docs)
    if fetch_problem is not None:
        logger.error(
            "Systemic-fetch guard tripped for %s: %s. Corpus left untouched.",
            adapter.source_corpus, fetch_problem,
        )
        summary.status = "aborted_fetch"
        summary.error = fetch_problem
        await db.rollback()
        await _record_run(db, summary)
        return summary

    # 3. Load the DB index + classify.
    existing = await _load_index(db, adapter.source_corpus)
    seen: set[str] = set()
    new_docs: list[tuple[SourceDoc, str]] = []
    changed_docs: list[tuple[SourceDoc, str]] = []
    unchanged_ids: list[str] = []
    for d in docs:
        seen.add(d.source_id)
        h = _content_hash(d.text)
        if d.source_id not in existing:
            new_docs.append((d, h))
        elif existing[d.source_id] != h:
            changed_docs.append((d, h))
        else:
            unchanged_ids.append(d.source_id)
    summary.docs_new = len(new_docs)
    summary.docs_changed = len(changed_docs)
    summary.docs_unchanged = len(unchanged_ids)

    # 4. Expand + embed NEW/CHANGED up front, BEFORE any delete. UNCHANGED docs are
    #    never re-embedded (cheap refresh — the whole point of the hash diff).
    to_ingest = new_docs + changed_docs
    expanded: list[dict] = []
    for d, h in to_ingest:
        for sub_text in ks._split_text_for_embedding(d.text):
            expanded.append({"doc": d, "hash": h, "text": sub_text})
    texts = [e["text"] for e in expanded]
    embeddings = await ks._embed_all_resilient(texts) if texts else []
    embedded = sum(1 for e in embeddings if e is not None)
    summary.embed_skipped = len(texts) - embedded

    # 5. Systemic-embed guard (reused #150): if the gateway is down, abort WITHOUT
    #    deleting so the existing corpus survives for retry. No-op when no vectors
    #    are expected (keyword-fallback corpus).
    try:
        ks._guard_systemic_embedding(
            source=adapter.source_corpus,
            parsed=len(texts),
            embedded=embedded,
            has_vector=has_vector,
        )
    except ks.SystemicEmbeddingError as exc:
        summary.status = "aborted_embed"
        summary.error = str(exc)
        await db.rollback()
        await _record_run(db, summary)
        return summary

    # 6. Mass-purge circuit-breaker — computed BEFORE mutating anything.
    absent = set(existing) - seen
    if adapter.purge_policy is PurgePolicy.DELETE and existing:
        fraction = len(absent) / len(existing)
        if fraction > _MASS_PURGE_MAX_FRACTION:
            logger.error(
                "Mass-purge breaker tripped for %s: would purge %d/%d docs (%.0f%% > "
                "%.0f%%). Aborting the whole run; corpus preserved.",
                adapter.source_corpus, len(absent), len(existing),
                fraction * 100, _MASS_PURGE_MAX_FRACTION * 100,
            )
            summary.status = "aborted_purge"
            summary.error = (
                f"mass-purge breaker: {len(absent)}/{len(existing)} docs would be "
                f"purged ({fraction:.0%} > {_MASS_PURGE_MAX_FRACTION:.0%})"
            )
            await db.rollback()
            await _record_run(db, summary)
            return summary

    # 7. Mutate — a single transaction, embed-before-delete already satisfied.
    #    a. CHANGED: drop the old chunks of each changed doc (scoped, not corpus-wide).
    changed_ids = [d.source_id for d, _ in changed_docs]
    if changed_ids:
        await db.execute(
            delete(RegulationChunk).where(
                RegulationChunk.source_corpus == adapter.source_corpus,
                RegulationChunk.source_id.in_(changed_ids),
            )
        )
    #    b. Insert NEW + CHANGED chunk rows (skip a row that failed to embed only
    #       when vectors are expected; in the keyword-fallback path all rows land).
    for row, embedding in zip(expanded, embeddings, strict=True):
        if has_vector and embedding is None:
            continue
        await _insert_chunk_row(
            db,
            doc=row["doc"],
            sub_text=row["text"],
            content_hash=row["hash"],
            run_id=run_id,
            embedding=embedding,
            has_vector=has_vector,
            now=now,
        )
        summary.rows_inserted += 1
    #    c. UNCHANGED: bump last_seen_at (+ run id) only — no re-embed.
    if unchanged_ids:
        await db.execute(
            update(RegulationChunk)
            .where(
                RegulationChunk.source_corpus == adapter.source_corpus,
                RegulationChunk.source_id.in_(unchanged_ids),
            )
            .values(last_seen_at=now, ingestion_run_id=str(run_id))
        )
    #    d. ABSENT: purge per policy (DELETE only; RETAIN never purges on absence).
    if adapter.purge_policy is PurgePolicy.DELETE and absent:
        await db.execute(
            delete(RegulationChunk).where(
                RegulationChunk.source_corpus == adapter.source_corpus,
                RegulationChunk.source_id.in_(list(absent)),
            )
        )
        summary.docs_purged += len(absent)
    #    e. Retention sweep for RETAIN sources with a window (full reconcile only).
    if (
        adapter.purge_policy is PurgePolicy.RETAIN
        and adapter.retention_months
        and mode is IngestionMode.RECONCILE
    ):
        summary.docs_purged += await _retention_sweep(
            db, adapter.source_corpus, adapter.retention_months, now
        )
    #    f. One-time legacy (NULL source_id) sweep on a full reconcile.
    if mode is IngestionMode.RECONCILE:
        await _purge_legacy_untracked(db, adapter.source_corpus, run_id)

    # 8. Commit everything + record the successful run in the same transaction.
    summary.status = "success"
    await _record_run(db, summary)
    logger.info(
        "Reconcile %s (%s) done: seen=%d new=%d changed=%d unchanged=%d purged=%d "
        "embed_skipped=%d rows_inserted=%d",
        adapter.source_corpus, mode.value, summary.docs_seen, summary.docs_new,
        summary.docs_changed, summary.docs_unchanged, summary.docs_purged,
        summary.embed_skipped, summary.rows_inserted,
    )
    return summary
