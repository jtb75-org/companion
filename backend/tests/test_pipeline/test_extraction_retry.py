"""LLM extraction retries a transient truncated/unparseable response before
dropping to the lossy regex fallback (Gemini intermittently truncates ~10-20%)."""

from __future__ import annotations

import app.pipeline.extraction as extraction

_GOOD = (
    '{"sender": "CITY OF KIRKWOOD", "amount_due": -10.15, '
    '"due_date": "2026-07-22", "account_number_masked": "****5369"}'
)
_TRUNCATED = '{"sender": "CITY OF KIRKWOOD", "amount_due": -10.15, "account_number_masked":'


class _ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, **_kwargs):
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return r


async def test_llm_extract_recovers_on_retry(monkeypatch):
    """First response is truncated; the retry succeeds → full fields returned,
    NOT a drop to regex."""
    llm = _ScriptedLLM([_TRUNCATED, _GOOD])
    monkeypatch.setattr("app.conversation.llm.get_llm_client", lambda: llm)

    result = await extraction._llm_extract("some bill OCR text", "bill", None)

    assert result is not None, "should recover, not fall back to regex"
    assert result["amount_due"] == -10.15
    assert result["due_date"] == "2026-07-22"
    assert llm.calls == 2, "should have retried exactly once"


async def test_llm_extract_falls_back_after_max_attempts(monkeypatch):
    """Persistent truncation → None (caller then uses regex), and it stops at
    the attempt cap rather than spinning."""
    llm = _ScriptedLLM([_TRUNCATED])
    monkeypatch.setattr("app.conversation.llm.get_llm_client", lambda: llm)

    result = await extraction._llm_extract("some bill OCR text", "bill", None)

    assert result is None
    assert llm.calls == extraction._LLM_EXTRACT_MAX_ATTEMPTS


async def test_llm_extract_non_parse_error_does_not_retry(monkeypatch):
    """A genuine client/network error is NOT retried (would just spin) — one call,
    straight to regex."""
    class _BoomLLM:
        def __init__(self):
            self.calls = 0

        async def generate(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("vertex unavailable")

    llm = _BoomLLM()
    monkeypatch.setattr("app.conversation.llm.get_llm_client", lambda: llm)

    result = await extraction._llm_extract("some bill OCR text", "bill", None)

    assert result is None
    assert llm.calls == 1, "non-parse errors must not retry-spin"
