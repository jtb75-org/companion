"""The non-streaming member chat (`/message`, tool path) must report a coarse
cut category when the FINAL answer was cut off, so the client can show a soft
"response stopped early" note. A block with no text still degrades to the calm
fallback with no cut flag."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.v1.conversation as conv
from app.conversation.llm import LLM_FALLBACK_MESSAGE, GeminiClient


@pytest.fixture(autouse=True)
def _identity_safety(monkeypatch):
    # Isolate the cut logic from the safety layer's text transforms.
    import app.conversation.safety as safety

    monkeypatch.setattr(safety, "check_response_safety", lambda text, uid: text)


def _final_response(finish: str, text: str):
    """A final (no-tool-call) Gemini response with a given finish_reason + text."""
    candidate = SimpleNamespace(
        finish_reason=SimpleNamespace(name=finish),
        content=SimpleNamespace(parts=[SimpleNamespace(function_call=None)]),
    )
    resp = SimpleNamespace()
    resp.candidates = [candidate]
    resp.text = text
    return resp


class _BlockedEmptyResponse:
    """A blocked candidate whose `.text` raises, like Vertex on a no-text block."""

    def __init__(self, finish: str):
        self.candidates = [
            SimpleNamespace(
                finish_reason=SimpleNamespace(name=finish),
                content=SimpleNamespace(parts=[SimpleNamespace(function_call=None)]),
            )
        ]

    @property
    def text(self):
        raise ValueError("blocked: no text part")


async def _run(response) -> tuple[str, str | None]:
    llm = GeminiClient()
    llm.generate_with_tools = AsyncMock(return_value=response)
    return await conv._generate_with_tools(
        llm, "sys", [{"role": "user", "content": "hi"}], None, "u1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish,expected_cut",
    [
        ("STOP", None),
        ("MAX_TOKENS", "length"),
        ("SAFETY", "content"),
        ("RECITATION", "content"),
        ("PROHIBITED_CONTENT", "content"),
    ],
)
async def test_cut_reason_detection(finish, expected_cut):
    text, cut = await _run(_final_response(finish, "the answer"))
    assert text == "the answer"
    assert cut == expected_cut


@pytest.mark.asyncio
async def test_blocked_empty_returns_fallback_and_no_cut():
    # A block with no text → calm fallback, NO cut flag (no partial to explain).
    text, cut = await _run(_BlockedEmptyResponse("SAFETY"))
    assert text == LLM_FALLBACK_MESSAGE
    assert cut is None
