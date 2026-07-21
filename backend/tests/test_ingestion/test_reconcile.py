"""Hermetic tests for the regulation reconcile engine + eCFR/FedReg adapters.

Embeddings are stubbed by the autouse ``stub_ai_backends`` fixture (and re-stubbed
per-test where a call count matters); adapter HTTP is mocked. No network, no pgvector
required — CI's postgres has no vector extension, exactly like the reg keyword path.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db import session as db_module
from app.ingestion.adapters.ecfr import ECFRAdapter
from app.ingestion.adapters.fedreg import FederalRegisterAdapter
from app.ingestion.reconciler import _content_hash, run_source
from app.ingestion.types import Adapter, IngestionMode, PurgePolicy, SourceDoc
from app.services import knowledge_service
from tests.conftest import requires_db

pytestmark = requires_db


# ── Fixtures + helpers ─────────────────────────────────────────────────────────


async def _wipe():
    async with db_module.async_session_factory() as s:
        await s.execute(text("DELETE FROM disability_reg_chunks"))
        await s.execute(text("DELETE FROM reg_ingestion_runs"))
        await s.commit()


@pytest.fixture(autouse=True)
async def cleanup():
    await _wipe()
    yield
    await _wipe()


class FakeAdapter(Adapter):
    """A source-agnostic adapter that yields a fixed doc list (or raises)."""

    def __init__(
        self,
        docs: Iterable[SourceDoc],
        *,
        source_corpus: str = "eCFR",
        purge_policy: PurgePolicy = PurgePolicy.DELETE,
        min_expected_docs: int = 0,
        retention_months: int = 0,
        raise_exc: Exception | None = None,
    ) -> None:
        self.source_corpus = source_corpus
        self.purge_policy = purge_policy
        self.min_expected_docs = min_expected_docs
        self.retention_months = retention_months
        self._docs = list(docs)
        self._raise = raise_exc

    async def list_documents(self, mode: IngestionMode) -> Iterable[SourceDoc]:
        if self._raise is not None:
            raise self._raise
        return list(self._docs)


def _doc(
    source_id: str,
    body: str,
    *,
    corpus: str = "eCFR",
    citation: str | None = None,
    program: str = "SSDI",
    eff: datetime | None = None,
) -> SourceDoc:
    return SourceDoc(
        source_id=source_id,
        text=body,
        metadata={
            "jurisdiction": "US_Federal",
            "source_corpus": corpus,
            "source_url": "https://example.gov/reg",
            "citation": citation or source_id,
            "program": program,
            "effective_date": eff,
        },
    )


async def _seed(
    source_id: str,
    body: str,
    *,
    corpus: str = "eCFR",
    citation: str | None = None,
    program: str = "SSDI",
    eff: datetime | None = None,
    last_seen: datetime | None = None,
) -> None:
    """Insert one TRACKED chunk row (content_hash = hash(body)) as a prior run would."""
    async with db_module.async_session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO disability_reg_chunks "
                "(id, jurisdiction, source_corpus, source_url, citation, program, "
                " text_content, token_count, effective_date, source_id, content_hash, "
                " last_seen_at) "
                "VALUES (:id, :jur, :corpus, :url, :cit, :prog, :txt, :tok, :eff, "
                " :sid, :hash, :seen)"
            ),
            {
                "id": str(uuid.uuid4()),
                "jur": "US_Federal",
                "corpus": corpus,
                "url": "https://example.gov/reg",
                "cit": citation or source_id,
                "prog": program,
                "txt": body,
                "tok": len(body) // 4,
                "eff": eff or datetime.now(UTC),
                "sid": source_id,
                "hash": _content_hash(body),
                "seen": last_seen or datetime.now(UTC) - timedelta(days=90),
            },
        )
        await s.commit()


async def _rows(corpus: str) -> list:
    async with db_module.async_session_factory() as s:
        res = await s.execute(
            text(
                "SELECT source_id, text_content, last_seen_at FROM disability_reg_chunks "
                "WHERE source_corpus = :c ORDER BY source_id"
            ),
            {"c": corpus},
        )
        return res.fetchall()


async def _latest_run(source: str):
    async with db_module.async_session_factory() as s:
        res = await s.execute(
            text(
                "SELECT status, docs_seen, docs_new, docs_changed, docs_unchanged, "
                "docs_purged, error FROM reg_ingestion_runs WHERE source = :s "
                "ORDER BY started_at DESC LIMIT 1"
            ),
            {"s": source},
        )
        return res.fetchone()


def _count_embed(monkeypatch) -> list[str]:
    """Replace the embed seam with a capture; returns the growing list of embedded texts."""
    captured: list[str] = []

    async def _capturing_embed(texts):
        captured.extend(texts)
        return [[0.1] * 768 for _ in texts]

    monkeypatch.setattr(knowledge_service, "embed_documents", _capturing_embed)
    return captured


# ── 1. Reconcile classification: new / changed / unchanged / absent ─────────────


async def test_reconcile_applies_new_changed_unchanged_and_purge(monkeypatch):
    """A run with one of each kind: NEW inserts, CHANGED re-embeds+replaces, UNCHANGED
    only touches last_seen_at (NO re-embed), ABSENT is purged. Counts recorded."""
    embedded = _count_embed(monkeypatch)

    old_seen = datetime.now(UTC) - timedelta(days=90)
    await _seed("A", "alpha section text", last_seen=old_seen)
    await _seed("B", "bravo ORIGINAL text", last_seen=old_seen)
    await _seed("C", "charlie section text", last_seen=old_seen)
    await _seed("D", "delta section text", last_seen=old_seen)  # will be absent → purge

    adapter = FakeAdapter(
        [
            _doc("A", "alpha section text"),        # unchanged (same hash)
            _doc("B", "bravo UPDATED text"),        # changed (hash differs)
            _doc("C", "charlie section text"),      # unchanged
            _doc("E", "echo brand new text"),       # new
        ]
    )

    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "success"
    assert (summary.docs_new, summary.docs_changed, summary.docs_unchanged,
            summary.docs_purged) == (1, 1, 2, 1)
    assert summary.docs_seen == 4

    rows = await _rows("eCFR")
    by_id = {r.source_id: r for r in rows}
    assert set(by_id) == {"A", "B", "C", "E"}          # D purged, E added
    assert by_id["B"].text_content == "bravo UPDATED text"  # replaced
    assert by_id["A"].last_seen_at > old_seen              # touched
    assert by_id["C"].last_seen_at > old_seen

    # UNCHANGED docs are NOT re-embedded; only the changed + new ones are.
    assert "bravo UPDATED text" in embedded
    assert "echo brand new text" in embedded
    assert "alpha section text" not in embedded
    assert "charlie section text" not in embedded

    run = await _latest_run("eCFR")
    assert run.status == "success"
    assert (run.docs_seen, run.docs_new, run.docs_changed, run.docs_unchanged,
            run.docs_purged) == (4, 1, 1, 2, 1)


# ── 2. Systemic-fetch guard: bad pull never purges ──────────────────────────────


async def test_fetch_raise_preserves_corpus_and_marks_failed():
    """Adapter raises → abort before any delete; corpus preserved, run marked failed."""
    await _seed("A", "must survive a failed fetch")
    adapter = FakeAdapter([], raise_exc=RuntimeError("gateway timeout"))

    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "aborted_fetch"
    assert summary.ok is False
    rows = await _rows("eCFR")
    assert {r.source_id for r in rows} == {"A"}   # untouched
    run = await _latest_run("eCFR")
    assert run.status == "aborted_fetch"
    assert run.error


async def test_fetch_zero_docs_delete_source_aborts():
    """A purge-on-absence source yielding 0 docs is a broken fetch → abort, no purge."""
    await _seed("A", "must survive an empty fetch")
    adapter = FakeAdapter([], purge_policy=PurgePolicy.DELETE)

    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "aborted_fetch"
    rows = await _rows("eCFR")
    assert {r.source_id for r in rows} == {"A"}


# ── 3. Mass-purge circuit-breaker ───────────────────────────────────────────────


async def test_mass_purge_breaker_preserves_corpus():
    """>30% of the corpus would be purged (4/5 absent) → abort the whole run, preserve all."""
    for sid in ("A", "B", "C", "D", "E"):
        await _seed(sid, f"{sid} body text")

    # Only A is still present at the source; B,C,D,E look absent (80% > 30%).
    adapter = FakeAdapter([_doc("A", "A body text")], purge_policy=PurgePolicy.DELETE)

    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "aborted_purge"
    assert summary.error
    rows = await _rows("eCFR")
    assert {r.source_id for r in rows} == {"A", "B", "C", "D", "E"}  # nothing purged
    run = await _latest_run("eCFR")
    assert run.status == "aborted_purge"


# ── 4. eCFR adapter: source_id = citation, purge-on-absence deletes ─────────────


class _MockResp:
    def __init__(self, text_body="", status_code=200, json_data=None):
        self.text = text_body
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json or {}


def _ecfr_html(section_ids: list[str]) -> str:
    sections = "\n".join(
        f'<div class="section" id="{sid}"><h4>§ {sid} Heading.</h4>'
        f"<p>Body for section {sid} kept short.</p></div>"
        for sid in section_ids
    )
    return f'<div class="part" id="part-404">{sections}</div>'


async def test_ecfr_purge_on_absence_deletes_removed_citation(monkeypatch):
    """eCFR reconcile: a citation no longer in the source is DELETED (purge-on-absence)."""
    _count_embed(monkeypatch)
    for sid in ("404.1", "404.2", "404.3", "404.4"):
        await _seed(f"20 CFR § {sid}", f"seeded body {sid}", citation=f"20 CFR § {sid}")

    # The live source no longer contains 404.4 (one absent of four → 25%, under the breaker).
    async def mock_get(*args, **kwargs):
        return _MockResp(text_body=_ecfr_html(["404.1", "404.2", "404.3"]))

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    adapter = ECFRAdapter(parts=[404], min_expected_docs=1)
    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "success"
    assert summary.docs_purged == 1
    rows = await _rows("eCFR")
    assert {r.source_id for r in rows} == {"20 CFR § 404.1", "20 CFR § 404.2", "20 CFR § 404.3"}


# ── 5. Federal Register: RETAIN (no absence-purge) + retention sweep + append ────


async def test_fedreg_absence_does_not_delete_and_appends_new(monkeypatch):
    """FR incremental: existing docs absent from the recent feed are NOT purged, and a new
    document number is appended."""
    _count_embed(monkeypatch)
    recent = datetime.now(UTC) - timedelta(days=30)
    await _seed("2020-0001", "old rule one", corpus="Federal_Register",
                citation="Federal Register Vol. 2020-0001", program="Both", eff=recent)
    await _seed("2020-0002", "old rule two", corpus="Federal_Register",
                citation="Federal Register Vol. 2020-0002", program="Both", eff=recent)

    mock_json = {
        "results": [
            {
                "title": "Brand New SSI Rule",
                "abstract": "A newly published rule to be appended by document number.",
                "document_number": "2024-9999",
                "publication_date": "2024-07-15",
                "html_url": "https://www.federalregister.gov/documents/2024/07/15/2024-9999",
            }
        ]
    }

    async def mock_get(*args, **kwargs):
        return _MockResp(status_code=200, json_data=mock_json)

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    adapter = FederalRegisterAdapter()
    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.INCREMENTAL)

    assert summary.status == "success"
    assert summary.docs_new == 1
    assert summary.docs_purged == 0  # absence never purges FR
    rows = await _rows("Federal_Register")
    assert {r.source_id for r in rows} == {"2020-0001", "2020-0002", "2024-9999"}


async def test_fedreg_retention_sweep_removes_old_doc(monkeypatch):
    """FR reconcile: the 24-month retention sweep removes a doc older than the window while
    keeping a recent one; absence itself still does not purge."""
    _count_embed(monkeypatch)
    old = datetime.now(UTC) - timedelta(days=30 * 30)   # ~30 months → outside 24-mo window
    fresh = datetime.now(UTC) - timedelta(days=30)       # inside the window
    await _seed("2019-0001", "aged out rule", corpus="Federal_Register",
                citation="Federal Register Vol. 2019-0001", program="Both", eff=old)
    await _seed("2024-0001", "recent rule", corpus="Federal_Register",
                citation="Federal Register Vol. 2024-0001", program="Both", eff=fresh)

    # Empty recent feed: nothing new, and (RETAIN) nothing purged by absence — only the
    # retention sweep acts.
    async def mock_get(*args, **kwargs):
        return _MockResp(status_code=200, json_data={"results": []})

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    adapter = FederalRegisterAdapter()
    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "success"
    assert summary.docs_purged == 1  # the aged-out doc, via retention (not absence)
    rows = await _rows("Federal_Register")
    assert {r.source_id for r in rows} == {"2024-0001"}


# ── 6. Legacy (pre-reconcile) NULL source_id rows are swept on full reconcile ────


async def test_reconcile_sweeps_legacy_untracked_rows(monkeypatch):
    """A pre-migration row (NULL source_id) for the same corpus is removed once a healthy
    reconcile repopulates the corpus with tracked rows — so the first run can't double it."""
    _count_embed(monkeypatch)
    # Legacy untracked row (no source_id / content_hash), as prod has today.
    async with db_module.async_session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO disability_reg_chunks "
                "(id, jurisdiction, source_corpus, source_url, citation, program, "
                " text_content, token_count, effective_date) "
                "VALUES (:id, :jur, :corpus, :url, :cit, :prog, :txt, :tok, :eff)"
            ),
            {
                "id": str(uuid.uuid4()),
                "jur": "US_Federal",
                "corpus": "eCFR",
                "url": "https://example.gov/reg",
                "cit": "20 CFR § 404.1",
                "prog": "SSDI",
                "txt": "legacy untracked body",
                "tok": 5,
                "eff": datetime.now(UTC),
            },
        )
        await s.commit()

    adapter = FakeAdapter([_doc("20 CFR § 404.1", "fresh tracked body",
                                citation="20 CFR § 404.1")])
    async with db_module.async_session_factory() as db:
        summary = await run_source(db, adapter, IngestionMode.RECONCILE)

    assert summary.status == "success"
    rows = await _rows("eCFR")
    # Exactly one row remains: the tracked one; the legacy NULL-source_id row is gone.
    assert len(rows) == 1
    assert rows[0].source_id == "20 CFR § 404.1"
    assert rows[0].text_content == "fresh tracked body"
