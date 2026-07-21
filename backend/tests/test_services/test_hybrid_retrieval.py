"""Hybrid (BM25 + vector, RRF-fused) retrieval eval harness — REAL DB required.

These tests are the seed of a retrieval-quality eval set. They assert that HYBRID
retrieval surfaces the correct regulation section in the top-k for queries where
PURE VECTOR search fails on phrasing — most importantly the live regression:

    "What is the five-step evaluation?"  →  20 CFR § 404.1520

which pure cosine retrieval missed (it returned tangential sections and the model
DECLINED), because the exact chunk was not in the vector top-k. A BM25 leg matches
the "five-step" tokens / the citation directly, and RRF lifts it into the fused
top-k.

REQUIREMENTS — these need BOTH pgvector AND ParadeDB pg_search (the BM25 index from
migration 047). CI's postgres has neither (see the standing "CI has no pgvector"
note), so every test here SKIPS in CI. To run them against a prod-like DB
(ParadeDB with migrations applied), point the app at it and run this module, e.g.:

    docker run -d --name paradedb -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=companion_test -p 5459:5432 paradedb/paradedb:latest
    COMPANION_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5459/companion_test \\
        .venv/bin/alembic upgrade head
    COMPANION_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5459/companion_test \\
        .venv/bin/pytest tests/test_services/test_hybrid_retrieval.py -v

The embedding gateway is NOT needed: each chunk is inserted with an explicit,
controlled 768-dim embedding and ``embed_query`` is monkeypatched to a chosen query
vector, so the semantic leg is fully deterministic (no network, no real model). The
scenarios are constructed so the SEMANTIC leg genuinely misses the target (its
cosine falls under the floor) and only the LEXICAL leg can rescue it — which is the
exact failure mode being fixed.
"""

import uuid

import pytest
from sqlalchemy import text

from app.db import session as db_module
from app.services import knowledge_service
from tests.conftest import requires_db

pytestmark = requires_db

_EMBED_DIM = 768


def _unit_vec(idx: int, dim: int = _EMBED_DIM) -> list[float]:
    """A one-hot 768-dim unit vector (1.0 at ``idx``). Two different one-hot vectors
    are orthogonal → cosine similarity 0.0; the same one-hot vector → 1.0. That lets a
    test place a chunk either 'near' or 'far' from the query in embedding space with
    full control."""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


async def _pg_search_available() -> bool:
    async with db_module.async_session_factory() as s:
        vec = await knowledge_service.has_pgvector(s)
        if not vec:
            return False
        res = await s.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
        )
        if res.scalar() is None:
            return False
        res = await s.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE tablename = 'disability_reg_chunks' "
                "AND indexname = 'ix_disability_reg_chunks_bm25'"
            )
        )
        return res.scalar() is not None


async def _reset_caps():
    # The service caches capability probes at module scope; clear them so this run's
    # real DB is re-detected rather than a prior no-DB verdict being reused.
    knowledge_service._has_vector_extension = None
    knowledge_service._has_pg_search_extension = None


@pytest.fixture(autouse=True)
async def _requires_pg_search():
    await _reset_caps()
    if not await _pg_search_available():
        pytest.skip("pgvector + pg_search BM25 index required (ParadeDB; run migration 047)")
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with db_module.async_session_factory() as s:
        await s.execute(text("DELETE FROM disability_reg_chunks"))
        await s.commit()


async def _insert(
    *, citation: str, text_content: str, embedding: list[float], program: str = "SSDI"
) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO disability_reg_chunks "
                "(id, jurisdiction, source_corpus, source_url, citation, program, "
                " text_content, token_count, effective_date, embedding) "
                "VALUES (:id,'US_Federal','eCFR','http://x',:cit,:prog,:txt,:tok,now(),"
                " CAST(:emb AS vector))"
            ),
            {
                "id": str(uuid.uuid4()),
                "cit": citation,
                "prog": program,
                "txt": text_content,
                "tok": len(text_content) // 4,
                "emb": str(embedding),
            },
        )
        await s.commit()


def _patch_query_vec(monkeypatch, vec: list[float]) -> None:
    """Force the query embedding, overriding the autouse stub. The semantic leg then
    ranks purely on our controlled geometry."""

    async def _embed_query(_text: str) -> list[float]:
        return vec

    monkeypatch.setattr(knowledge_service, "embed_query", _embed_query)


def _citations(results: list[dict]) -> list[str]:
    return [r["citation"] for r in results]


# ── The flagship regression: five-step ─────────────────────────────────────────


async def test_five_step_regression_vector_misses_hybrid_finds(monkeypatch):
    """Vector-only MISSES 20 CFR § 404.1520 (its cosine is below the floor); hybrid
    surfaces it in the top-k via the BM25 lexical leg. This is the live failure."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)

    # Target: contains the "five-step" wording but sits ORTHOGONAL to the query in
    # embedding space (cosine 0.0 → below the semantic floor, so vector drops it).
    await _insert(
        citation="20 CFR § 404.1520",
        text_content=(
            "We use a five-step sequential evaluation process to determine "
            "whether you are disabled."
        ),
        embedding=_unit_vec(5),
    )
    # Distractors: query-ALIGNED embeddings (cosine 1.0, dominate the vector leg) but
    # NO "five-step" wording (invisible to BM25 for this query).
    for cit, sect in [
        ("20 CFR § 416.1407", "notice of reconsideration determinations"),
        ("20 CFR § 416.924", "how we determine disability for a child"),
        ("20 CFR § 404.967", "the Appeals Council review procedure"),
    ]:
        await _insert(citation=cit, text_content=sect, embedding=query_vec, program="SSI")

    async with db_module.async_session_factory() as s:
        # Semantic leg alone: the target is filtered out by the cosine floor.
        vec_only = await knowledge_service._vector_search(
            s, str(query_vec), None, knowledge_service._CANDIDATE_POOL
        )
        assert "20 CFR § 404.1520" not in [r.citation for r in vec_only]

        # Hybrid: BM25 finds the five-step section and RRF lifts it into the top-k.
        hybrid = await knowledge_service.search_regulations(
            s, "What is the five-step evaluation?", None, limit=5
        )
    assert "20 CFR § 404.1520" in _citations(hybrid)


@pytest.mark.parametrize(
    "query",
    ["five step", "five-step sequential evaluation", "What is the five-step evaluation?"],
)
async def test_five_step_phrasings_all_surface_the_section(monkeypatch, query):
    """Every phrasing of the five-step question surfaces 404.1520 in the top-k."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)
    await _insert(
        citation="20 CFR § 404.1520",
        text_content=(
            "We use a five-step sequential evaluation process to determine "
            "whether you are disabled."
        ),
        embedding=_unit_vec(5),
    )
    await _insert(
        citation="20 CFR § 416.924",
        text_content="how we determine disability for a child",
        embedding=query_vec,
        program="SSI",
    )
    async with db_module.async_session_factory() as s:
        results = await knowledge_service.search_regulations(s, query, None, limit=5)
    assert "20 CFR § 404.1520" in _citations(results)


# ── Appeals-deadline phrasing ──────────────────────────────────────────────────


async def test_appeal_deadline_surfaces_reconsideration_section(monkeypatch):
    """"How long do I have to appeal a denial?" surfaces the appeals-deadline section
    even though its embedding is far from the query — BM25 matches appeal/denial."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)
    await _insert(
        citation="20 CFR § 404.909",
        text_content=(
            "If you disagree with our denial, you may appeal by requesting "
            "reconsideration within 60 days after you receive the notice."
        ),
        embedding=_unit_vec(7),
    )
    await _insert(
        citation="20 CFR § 404.1520",
        text_content="the five-step sequential evaluation process",
        embedding=query_vec,
    )
    async with db_module.async_session_factory() as s:
        results = await knowledge_service.search_regulations(
            s, "How long do I have to appeal a denial?", None, limit=5
        )
    assert "20 CFR § 404.909" in _citations(results)


# ── Citation-style query ───────────────────────────────────────────────────────


async def test_citation_style_query_hits_exact_section(monkeypatch):
    """A citation-style query ("404.1520") lands on that exact section via BM25 over
    the citation column, regardless of embedding geometry."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)
    await _insert(
        citation="20 CFR § 404.1520",
        text_content="the sequential evaluation process for disability",
        embedding=_unit_vec(9),
    )
    await _insert(
        citation="20 CFR § 416.924",
        text_content="child disability determination",
        embedding=query_vec,
        program="SSI",
    )
    async with db_module.async_session_factory() as s:
        results = await knowledge_service.search_regulations(s, "404.1520", None, limit=5)
    assert "20 CFR § 404.1520" in _citations(results)


# ── Graceful degradation ───────────────────────────────────────────────────────


async def test_falls_back_to_vector_when_pg_search_absent(monkeypatch):
    """If pg_search reports unavailable, retrieval degrades to vector-only (no BM25,
    no error) and still returns the semantically-close chunks."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)

    async def _no_pg_search(_db):
        return False

    monkeypatch.setattr(knowledge_service, "has_pg_search", _no_pg_search)
    await _insert(
        citation="20 CFR § 416.924",
        text_content="child disability determination",
        embedding=query_vec,
        program="SSI",
    )
    async with db_module.async_session_factory() as s:
        results = await knowledge_service.search_regulations(
            s, "child disability", None, limit=5
        )
    assert "20 CFR § 416.924" in _citations(results)


async def test_falls_back_to_vector_when_bm25_leg_errors(monkeypatch):
    """A runtime BM25 failure is caught, the aborted statement is rolled back, and
    retrieval continues vector-only rather than 500-ing."""
    query_vec = _unit_vec(0)
    _patch_query_vec(monkeypatch, query_vec)

    async def _boom(*_a, **_k):
        raise RuntimeError("pg_search blew up")

    monkeypatch.setattr(knowledge_service, "_bm25_search", _boom)
    await _insert(
        citation="20 CFR § 416.924",
        text_content="child disability determination",
        embedding=query_vec,
        program="SSI",
    )
    async with db_module.async_session_factory() as s:
        results = await knowledge_service.search_regulations(
            s, "child disability", None, limit=5
        )
    assert "20 CFR § 416.924" in _citations(results)
