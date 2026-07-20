"""Tests for the PUBLIC benefits-helper endpoint POST /public/knowledge/ask.

Hermetic: the LLM/embeddings are stubbed by the autouse ``stub_ai_backends``
fixture, and Redis is replaced with an in-memory fake so the anonymous
free-question quota is deterministic and offline. No auth, no PHI.

Coverage:
  * first N questions from a fresh anonymous session answer normally (disclaimer
    + citations present);
  * the (N+1)th returns gated=true with NO LLM/RAG call;
  * a returning session (same cookie) continues its count;
  * Redis-down → fail-CLOSED (gated, no LLM call);
  * over-long / empty question rejected before any LLM work;
  * the answer path touches only the public regulation corpus (no PHI).
"""

import uuid
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
from app.db import session as db_module
from app.main import app
from app.services import knowledge_service
from tests.conftest import requires_db

pytestmark = requires_db

_ASK_ENDPOINT = "/public/knowledge/ask"
_DISCLAIMER = knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER
_FREE_LIMIT = 3


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _cleanup_reg_chunks():
    async with db_module.async_session_factory() as s:
        await s.execute(text("DELETE FROM disability_reg_chunks"))
        await s.commit()


class _FakeRedis:
    """Minimal in-memory async Redis over a shared dict — enough for the anon quota
    (get / incr / expire / aclose). Mirrors decode_responses=True (get → str).

    ``expire_calls`` records every EXPIRE issued (key, ttl) so tests can assert the
    24h window is set exactly ONCE (on the first question) and never extended."""

    def __init__(self, store: dict, expire_calls: list | None = None):
        self.store = store
        self.expire_calls = expire_calls if expire_calls is not None else []

    async def get(self, key):
        val = self.store.get(key)
        return None if val is None else str(val)

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return True

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
async def _setup(monkeypatch):
    # Small, deterministic free allowance; cookie must round-trip over http in the
    # ASGI test client, so disable the Secure flag for the test.
    monkeypatch.setattr(settings, "public_knowledge_free_limit", _FREE_LIMIT)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    await _cleanup_reg_chunks()
    yield
    await _cleanup_reg_chunks()


def _use_fake_redis(monkeypatch) -> tuple[dict, list]:
    """Install the in-memory fake Redis. Returns (store, expire_calls) so a test can
    inspect the counter and every EXPIRE issued. Most tests use it only for its side
    effect and ignore the return."""
    store: dict = {}
    expire_calls: list = []
    monkeypatch.setattr(
        knowledge_service, "get_redis", lambda: _FakeRedis(store, expire_calls)
    )
    return store, expire_calls


async def _insert_chunk(*, citation: str, text_content: str, program: str = "SSDI") -> None:
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
                "url": "https://www.ecfr.gov/current/title-20/part-404",
                "cit": citation,
                "prog": program,
                "txt": text_content,
                "tok": len(text_content) // 4,
                "eff": datetime(2024, 1, 1),
            },
        )
        await s.commit()


# ── Free questions answer normally, then gate on the (N+1)th ────────────────────


async def test_free_questions_then_gate(monkeypatch):
    """The first N questions answer normally (disclaimer + citation), the (N+1)th
    gates with NO LLM/RAG call — all within ONE anonymous session (cookie round-trips)."""
    _use_fake_redis(monkeypatch)
    await _insert_chunk(
        citation="20 CFR § 404.1520",
        text_content=(
            "We use a five-step sequential evaluation process to determine disability."
        ),
    )

    async with _client() as ac:
        # First N questions: answered, grounded, decrementing remaining.
        for i in range(_FREE_LIMIT):
            resp = await ac.post(
                _ASK_ENDPOINT,
                json={"question": "five-step sequential evaluation"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["gated"] is False
            assert body["grounded"] is True
            assert body["questions_remaining"] == _FREE_LIMIT - (i + 1)
            # Safety-critical output enforced in code, not the model:
            assert _DISCLAIMER in body["answer"]
            assert body["disclaimer"] == _DISCLAIMER
            assert body["answer"].startswith("Provenance: As of ")
            assert "20 CFR § 404.1520" in body["citations"]
            # The stub reply carried no disclaimer/citation — proof it is code-added.
            assert "stubbed" in body["answer"].lower()

        # Anonymous session cookie was issued and is opaque (not a user/email).
        assert ac.cookies.get(settings.public_knowledge_anon_cookie_name)

        # (N+1)th: the LLM/RAG path must NOT run. Patch generate_rag_answer to explode.
        async def _explode(*args, **kwargs):
            raise AssertionError("generate_rag_answer ran on a gated (exhausted) request")

        monkeypatch.setattr(knowledge_service, "generate_rag_answer", _explode)

        resp = await ac.post(
            _ASK_ENDPOINT, json={"question": "five-step sequential evaluation"}
        )
        assert resp.status_code == 200, resp.text
        gated = resp.json()
        assert gated["gated"] is True
        assert gated["grounded"] is False
        assert gated["questions_remaining"] == 0
        assert gated["citations"] == []
        assert gated["sources"] == []
        # No answer body, but the disclaimer is still present.
        assert gated["disclaimer"] == _DISCLAIMER
        assert "create a free account" in gated["answer"].lower()


async def test_returning_session_continues_count(monkeypatch):
    """A returning browser sending its cookie continues the SAME count; a fresh
    session (no cookie) starts over."""
    _use_fake_redis(monkeypatch)
    await _insert_chunk(
        citation="20 CFR § 404.1520",
        text_content="Five-step sequential evaluation process.",
    )

    async with _client() as ac:
        r1 = await ac.post(_ASK_ENDPOINT, json={"question": "five-step"})
        assert r1.json()["questions_remaining"] == _FREE_LIMIT - 1
        anon = ac.cookies.get(settings.public_knowledge_anon_cookie_name)

        r2 = await ac.post(_ASK_ENDPOINT, json={"question": "five-step"})
        # Count continued (did not reset) — proves the cookie round-tripped.
        assert r2.json()["questions_remaining"] == _FREE_LIMIT - 2
        assert ac.cookies.get(settings.public_knowledge_anon_cookie_name) == anon

    # A brand-new client (no cookie) starts a FRESH allowance.
    async with _client() as fresh:
        r3 = await fresh.post(_ASK_ENDPOINT, json={"question": "five-step"})
        assert r3.json()["questions_remaining"] == _FREE_LIMIT - 1


async def test_ttl_set_once_and_not_extended(monkeypatch):
    """The 24h window is anchored to the FIRST question: EXPIRE is issued exactly
    once (when INCR creates the key) and never again — not on later answered
    questions, and not on the gated over-limit request. This keeps a steady trickle
    from sliding the window forward forever."""
    _store, expire_calls = _use_fake_redis(monkeypatch)
    await _insert_chunk(
        citation="20 CFR § 404.1520",
        text_content="Five-step sequential evaluation process.",
    )

    async with _client() as ac:
        # Exhaust the allowance AND go one past it (gated), all in one session.
        for _ in range(_FREE_LIMIT + 1):
            await ac.post(_ASK_ENDPOINT, json={"question": "five-step"})

    # Despite _FREE_LIMIT+1 increments (the last one gated), EXPIRE fired only once.
    assert len(expire_calls) == 1
    key, ttl = expire_calls[0]
    assert key.startswith("knowledge:anon:")
    assert ttl == settings.public_knowledge_quota_ttl_seconds


async def test_increment_is_atomic_distinct_counts(monkeypatch):
    """Each call advances the counter exactly once via INCR and the returned count IS
    the decision — there is no GET-then-INCR window where two calls for the same
    session read the same stale pre-increment value. Two back-to-back calls yield
    strictly distinct, decreasing remainders and a counter that advanced twice."""
    store, expire_calls = _use_fake_redis(monkeypatch)
    anon = "atomicsession0123456"
    ttl = settings.public_knowledge_quota_ttl_seconds

    g1, rem1 = await knowledge_service.check_and_increment_anon_quota(
        anon, limit=_FREE_LIMIT, ttl_seconds=ttl
    )
    g2, rem2 = await knowledge_service.check_and_increment_anon_quota(
        anon, limit=_FREE_LIMIT, ttl_seconds=ttl
    )

    assert (g1, rem1) == (False, _FREE_LIMIT - 1)
    assert (g2, rem2) == (False, _FREE_LIMIT - 2)
    # Counter advanced once per call (no double-count, no reuse of a stale read).
    assert store[f"knowledge:anon:{anon}"] == 2
    # TTL anchored on the first call only.
    assert expire_calls == [(f"knowledge:anon:{anon}", ttl)]


async def test_gated_boundary_is_exact(monkeypatch):
    """Boundary check on the service directly: the first ``limit`` calls are allowed
    (remaining counts down to 0), and every call after that gates with remaining 0 —
    even though INCR-first keeps bumping the counter on gated calls."""
    _store, _expire = _use_fake_redis(monkeypatch)
    anon = "boundarysession01234"
    ttl = settings.public_knowledge_quota_ttl_seconds

    results = [
        await knowledge_service.check_and_increment_anon_quota(
            anon, limit=_FREE_LIMIT, ttl_seconds=ttl
        )
        for _ in range(_FREE_LIMIT + 2)
    ]

    # First _FREE_LIMIT allowed, remaining decrements to 0.
    for i in range(_FREE_LIMIT):
        assert results[i] == (False, _FREE_LIMIT - (i + 1))
    # Everything past the limit is gated with 0 remaining.
    assert results[_FREE_LIMIT] == (True, 0)
    assert results[_FREE_LIMIT + 1] == (True, 0)


async def test_redis_down_fails_closed(monkeypatch):
    """Redis unavailable → GATE (fail-closed), and the LLM/RAG path must NOT run.

    This is the cost-control decision that makes the public endpoint safe: if we
    cannot count free questions we deny rather than hand out unmetered LLM calls."""

    def _boom():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(knowledge_service, "get_redis", _boom)

    async def _explode(*args, **kwargs):
        raise AssertionError("generate_rag_answer ran while the quota store was down")

    monkeypatch.setattr(knowledge_service, "generate_rag_answer", _explode)

    async with _client() as ac:
        resp = await ac.post(_ASK_ENDPOINT, json={"question": "five-step"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gated"] is True
    assert body["grounded"] is False
    assert body["questions_remaining"] == 0
    assert body["disclaimer"] == _DISCLAIMER


async def test_overlong_question_rejected(monkeypatch):
    """An over-long question is rejected before any embedding/LLM work."""
    _use_fake_redis(monkeypatch)

    async def _explode(*args, **kwargs):
        raise AssertionError("generate_rag_answer ran on an over-long, rejected question")

    monkeypatch.setattr(knowledge_service, "generate_rag_answer", _explode)

    too_long = "a" * (settings.public_knowledge_max_question_chars + 1)
    async with _client() as ac:
        resp = await ac.post(_ASK_ENDPOINT, json={"question": too_long})
    assert resp.status_code == 422


async def test_empty_question_rejected(monkeypatch):
    """An empty/whitespace-only question is rejected (schema min_length + endpoint)."""
    _use_fake_redis(monkeypatch)
    async with _client() as ac:
        resp = await ac.post(_ASK_ENDPOINT, json={"question": "   "})
    assert resp.status_code == 422


async def test_no_auth_and_only_regulation_corpus(monkeypatch):
    """The endpoint answers with NO auth headers/cookies (anonymous) and sources come
    only from the public regulation corpus — never a PHI/document_chunks path."""
    _use_fake_redis(monkeypatch)
    await _insert_chunk(
        citation="20 CFR § 416.110",
        text_content="SSI is a needs-based program for aged, blind, and disabled people.",
        program="SSI",
    )

    async with _client() as ac:
        resp = await ac.post(
            _ASK_ENDPOINT,
            json={"question": "what is the SSI program", "program": "SSI"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["grounded"] is True
    assert len(body["sources"]) >= 1
    # Every source is from the public federal-regulation corpus.
    for src in body["sources"]:
        assert src["source_corpus"] in {"eCFR", "Federal_Register"}
    assert "20 CFR § 416.110" in body["citations"]
