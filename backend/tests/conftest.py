"""Shared test configuration.

Reconfigure the database engine for testing to avoid asyncpg event loop
conflicts with httpx ASGITransport. Uses NullPool instead of the default
QueuePool so each request gets a fresh connection.

Also ensures a test user exists so the dev-mode auth bypass works.
When no database is available (local dev without Docker), DB-dependent
tests are skipped but pure unit tests still run.
"""

import asyncio
import os
import sys

# MUST run before anything imports google.auth — see the NOTE below.
#
# Constructing ANY google.cloud client (TTS, STT, Vision, DocumentAI, Vertex...) with no
# credentials present makes google.auth hunt for them, and the last place it looks is the
# GCE metadata server at 169.254.169.254. That address is unroutable off GCE, so the probe
# does not fail — it hangs and retries with exponential backoff. Measured in this suite:
# 11.95s per client construction, entirely inside google/auth/_exponential_backoff.py.
# That is what made test_conversation_lifecycle take 184s in CI (the TTS client, not the
# LLM, was the larger half). The clients all swallow the failure and degrade to None, so
# the tests PASSED the whole time and the cost was invisible.
#
# The stub_ai_backends fixture below patches the specific call sites we know about; this
# is the backstop for the one we don't — a new google.cloud client added later fails in
# ~0ms instead of silently costing 12s. Belt and braces, because the fixture depends on
# someone remembering to add the new call site to it.
#
# NOTE: google/auth/compute_engine/_metadata.py reads both of these at MODULE IMPORT time
# (into _METADATA_DEFAULT_TIMEOUT / _METADATA_DETECT_RETRIES), not per call. Setting them
# after that module is imported does NOTHING. Hence: top of conftest, above the app
# imports, which is the earliest hook pytest gives us.
os.environ.setdefault("GCE_METADATA_DETECT_RETRIES", "0")
os.environ.setdefault("GCE_METADATA_TIMEOUT", "1")

import pytest  # noqa: E402
from sqlalchemy import pool, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session as db_module  # noqa: E402

_db_available = False

try:
    # Replace the global engine with a NullPool engine for tests
    _test_engine = create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        poolclass=pool.NullPool,
    )

    _test_session_factory = async_sessionmaker(
        _test_engine,
        expire_on_commit=False,
    )

    # Monkey-patch the db module so the app uses our test engine
    db_module.engine = _test_engine
    db_module.async_session_factory = _test_session_factory

    async def _ensure_test_user():
        """Create a test user if the database is empty."""
        from app.models.user import User

        async with _test_session_factory() as session:
            result = await session.execute(select(User).limit(1))
            existing = result.scalar_one_or_none()
            if existing and not existing.first_name:
                existing.first_name = "Test"
                existing.last_name = "User"
                await session.commit()
            if existing is None:
                user = User(
                    email="test@companion.app",
                    first_name="Test",
                    last_name="User",
                    preferred_name="Test",
                    display_name="Test User",
                    primary_language="en",
                    voice_id="warm",
                    pace_setting="normal",
                    warmth_level="warm",
                )
                session.add(user)
                await session.commit()

    asyncio.get_event_loop().run_until_complete(_ensure_test_user())
    _db_available = True
except Exception as e:
    import warnings

    warnings.warn(
        f"Database not available, DB-dependent tests will be skipped: {e}",
        stacklevel=1,
    )


@pytest.fixture
def db_session():
    """Provide a database session, skip if DB unavailable."""
    if not _db_available:
        pytest.skip("Database not available")

    async def _get_session():
        async with _test_session_factory() as session:
            yield session

    return _get_session


requires_db = pytest.mark.skipif(
    not _db_available, reason="Database not available"
)


def _patch_everywhere(monkeypatch, source_module, name: str, replacement) -> None:
    """Rebind ``name`` to ``replacement`` in its source module AND in every module that
    already imported it by value.

    A module-level ``from app.conversation.tts import synthesize_speech`` binds its OWN
    reference at import time; patching only the source leaves that copy pointing at the
    real function. Both import styles are in use here — app/pipeline/* imports the LLM
    factory lazily inside functions (so patching the source is enough, it re-reads at
    call time), while app/api/v1/conversation.py, app/notifications/briefing.py and
    app/conversation/__init__.py bind at module level. Patching the source alone would
    silently miss all three, which is precisely the bug this whole fixture exists to
    prevent. So: patch the source, then sweep sys.modules for stale copies.

    The sweep covers modules imported BEFORE this runs; patching the source covers those
    imported AFTER (their `from ... import X` reads the already-patched attribute). Both
    matter: app/conversation/retrieval.py binds embed_query at module level but is itself
    imported lazily, so which case applies depends on test order.

    Identity-guarded: only rebinds a module whose attribute IS the original object, so a
    same-named but unrelated symbol elsewhere is never clobbered (per niru's review).
    Known gaps, both covered by tests/test_no_live_llm.py: aliased imports
    (`import X as y`) and direct client construction bypassing the factory.
    """
    original = getattr(source_module, name)
    monkeypatch.setattr(source_module, name, replacement)
    for module in list(sys.modules.values()):
        if module is source_module or module is None:
            continue
        if getattr(module, name, None) is original:
            monkeypatch.setattr(module, name, replacement, raising=False)


@pytest.fixture(autouse=True)
def stub_ai_backends(request, monkeypatch):
    """Replace every cloud-AI backend (LLM, TTS, STT) for every test. Autouse, opt-out.

    WHY AUTOUSE: the default has to be "no network". Every one of these clients swallows
    its own failure and degrades quietly (the LLM to _fallback_response, TTS/STT to
    None), so a test that reaches for the network still PASSES — it just costs ~12s of
    credential-discovery backoff and, if credentials ever existed on the runner, would
    make a real billable call. That failure is invisible by construction, so opting IN
    would leave every new test exposed by default. Measured before this: 184s in CI for
    one test asserting only status codes. See tests/stub_llm.py.

    Opt out with @pytest.mark.real_ai. Used by tests/test_pipeline/test_embedding_client.py
    and tests/test_conversation/test_retrieval.py, which test these very boundaries and
    mock their own network (via _make_client / _embed_query + a fake DB), so they are
    self-contained without this fixture and must not be clobbered by it.

    The GCE_METADATA_* backstop at the top of this file covers a client this fixture
    does not know about yet. This fixture is the precise control (deterministic canned
    values); that is the blunt one (fail fast rather than hang).
    """
    if request.node.get_closest_marker("real_ai"):
        return None

    from tests.stub_llm import (
        STUB_AUDIO,
        STUB_EMBEDDING,
        STUB_TRANSCRIPT,
        StubGeminiClient,
    )

    client = StubGeminiClient()

    import app.conversation.llm as llm_module
    import app.conversation.stt as stt_module
    import app.conversation.tts as tts_module
    import app.pipeline.embedding_client as embedding_module

    _patch_everywhere(monkeypatch, llm_module, "get_llm_client", lambda: client)

    # Embeddings: NOT a google client. openai.AsyncOpenAI against the LAN LiteLLM
    # gateway (192.168.0.104:4000, 60s timeout) — unroutable from CI, so it blocks the
    # full 60s, and prompt_builder swallows the failure.
    #
    # This covers the PIPELINE path (embed_documents, during ingestion). The conversation
    # path is short-circuited a layer higher by the retrieve_relevant_chunks stub below —
    # see the comment there for why the SQL must not run in a no-pgvector CI.
    async def _embed_query(text: str) -> list[float]:
        return list(STUB_EMBEDDING)

    async def _embed_documents(texts: list[str]) -> list[list[float]]:
        return [list(STUB_EMBEDDING) for _ in texts]

    _patch_everywhere(monkeypatch, embedding_module, "embed_query", _embed_query)
    _patch_everywhere(monkeypatch, embedding_module, "embed_documents", _embed_documents)

    # RAG retrieval is stubbed a layer ABOVE the embedding call, and that is deliberate.
    #
    # Migration 011 creates document_chunks.embedding only `if pgvector` is available.
    # CI's postgres:16-alpine does NOT ship pgvector, so in CI the column does not exist
    # and the similarity query CANNOT run — RAG is untestable there, with or without a
    # stub. Today that is masked: embed_query hangs for 60s, so the SQL is never reached.
    # Stubbing only embed_query would let the query finally run and fail with
    # UndefinedColumnError, and because _build_document_context catches Exception and
    # returns "", the failure would be swallowed while leaving the request's transaction
    # ABORTED — every later statement then dies with InFailedSQLTransactionError. That
    # trades a slow pass for a confusing failure, which is worse.
    #
    # So: return no chunks. Same observable outcome as today (no RAG context in the
    # prompt), minus the 60s. Restoring genuine RAG coverage needs a pgvector-capable
    # postgres image in CI (prod uses paradedb, which bundles it) — a separate change.
    async def _retrieve_relevant_chunks(db, user_id, query, *args, **kwargs):
        return []

    import app.conversation.retrieval as retrieval_module

    _patch_everywhere(
        monkeypatch, retrieval_module, "retrieve_relevant_chunks", _retrieve_relevant_chunks
    )

    # Return BYTES, not None. Returning None would take the "TTS unavailable" branch —
    # which is what CI has always silently tested — leaving the base64-encode path in
    # conversation.py:253 uncovered. Canned bytes exercise the success path instead.
    async def _synthesize_speech(text: str, voice_id: str = "warm") -> bytes:
        return STUB_AUDIO

    async def _transcribe_audio(audio_data: bytes) -> str:
        return STUB_TRANSCRIPT

    _patch_everywhere(monkeypatch, tts_module, "synthesize_speech", _synthesize_speech)
    _patch_everywhere(monkeypatch, stt_module, "transcribe_audio", _transcribe_audio)

    return client
