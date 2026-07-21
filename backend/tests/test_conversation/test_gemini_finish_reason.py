"""GeminiClient.generate must guard on finish_reason.

Vertex returns whatever partial text it produced when a generation is cut short
(MAX_TOKENS, SAFETY, RECITATION, …) — `response.text` yields a mid-sentence
fragment with no error. The public disability-benefits helper surfaced exactly
this: an answer truncated at "...However, if you are appealing". These tests pin
the guard so a content-blocked response is never served as a fragment, and a
clean STOP still passes text through.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.conversation.llm import GeminiClient


def _fake_response(finish_name: str, text: str) -> MagicMock:
    """A stand-in for a Vertex response with a given finish_reason + text."""
    candidate = SimpleNamespace(
        finish_reason=SimpleNamespace(name=finish_name),
        content=SimpleNamespace(parts=[SimpleNamespace(text=text)]),
    )
    resp = MagicMock()
    resp.candidates = [candidate]
    resp.text = text
    return resp


def _client_returning(finish_name: str, text: str) -> GeminiClient:
    client = GeminiClient()
    model = MagicMock()
    model.generate_content_async = AsyncMock(
        return_value=_fake_response(finish_name, text)
    )
    # Bypass Vertex init/model construction; inject the mock model.
    client._get_model = MagicMock(return_value=model)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_name",
    ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"],
)
async def test_blocked_finish_reason_returns_fallback_not_fragment(finish_name):
    fragment = "The first step in the appeals process is to request a Re"
    client = _client_returning(finish_name, fragment)

    out = await client.generate(
        system_prompt="cite the regs", messages=[{"role": "user", "content": "appeal?"}]
    )

    # The truncated/blocked fragment must NOT be served.
    assert fragment not in out
    assert "trouble" in out.lower()  # the deterministic fallback copy


@pytest.mark.asyncio
async def test_stop_passes_text_through():
    answer = "You may appeal through reconsideration, then a hearing [20 CFR 416.1429]."
    client = _client_returning("STOP", answer)

    out = await client.generate(
        system_prompt="cite the regs", messages=[{"role": "user", "content": "appeal?"}]
    )

    assert out == answer


@pytest.mark.asyncio
async def test_max_tokens_still_returns_text():
    # MAX_TOKENS is logged but not swallowed — a longer budget is the real fix, and
    # dropping a nearly-complete answer would be worse than surfacing it.
    text = "You may appeal via reconsideration [20 CFR 416.1407]."
    client = _client_returning("MAX_TOKENS", text)

    out = await client.generate(
        system_prompt="cite the regs", messages=[{"role": "user", "content": "appeal?"}]
    )

    assert out == text


# --- streaming path (generate_stream) ---------------------------------------


class _Chunk:
    """A stand-in Vertex stream chunk. `raises=True` models a text-less chunk
    (Vertex raises ValueError on `.text` for a safety/finish-only chunk)."""

    def __init__(self, text: str, finish: str | None = None, raises: bool = False):
        self._text = text
        self._raises = raises
        self.candidates = (
            [SimpleNamespace(finish_reason=SimpleNamespace(name=finish))]
            if finish
            else []
        )

    @property
    def text(self) -> str:
        if self._raises:
            raise ValueError("no text part in this chunk")
        return self._text


def _streaming_client(chunks: list[_Chunk]) -> GeminiClient:
    client = GeminiClient()

    async def _agen():
        for c in chunks:
            yield c

    stream = MagicMock()
    stream.__aiter__ = lambda self: _agen()
    model = MagicMock()
    model.generate_content_async = AsyncMock(return_value=stream)
    client._get_model = MagicMock(return_value=model)  # type: ignore[method-assign]
    return client


async def _collect(client: GeminiClient) -> list[str]:
    return [
        piece
        async for piece in client.generate_stream(
            system_prompt="cite the regs",
            messages=[{"role": "user", "content": "appeal?"}],
        )
    ]


@pytest.mark.asyncio
async def test_stream_normal_passes_chunks_through():
    out = await _collect(
        _streaming_client([_Chunk("You may "), _Chunk("appeal.", finish="STOP")])
    )
    assert "".join(out) == "You may appeal."
    assert "trouble" not in "".join(out).lower()


@pytest.mark.asyncio
async def test_stream_blocked_with_no_content_yields_fallback():
    # A stream blocked before emitting any text should still say something graceful.
    out = await _collect(
        _streaming_client([_Chunk("", finish="SAFETY", raises=True)])
    )
    assert "trouble" in "".join(out).lower()


@pytest.mark.asyncio
async def test_stream_blocked_after_emitting_does_not_append_fallback():
    # Streamed text can't be retracted; we must NOT tack the fallback onto a
    # partial that already reached the user.
    out = await _collect(
        _streaming_client(
            [_Chunk("partial answer "), _Chunk("", finish="RECITATION", raises=True)]
        )
    )
    assert out == ["partial answer "]
    assert "trouble" not in "".join(out).lower()
