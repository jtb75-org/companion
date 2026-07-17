"""Tripwire: the suite must never reach a real LLM.

The conftest `stub_llm` fixture patches get_llm_client in the source module and in every
already-imported module that bound it at import time. That sweep is correct for what is
loaded when the fixture runs, but it cannot see a module imported LATER, nor a caller
that constructs GeminiClient() directly instead of going through the factory.

Either mistake is silent and expensive: the test still passes (every failure path in
GeminiClient returns _fallback_response), it just quietly takes ~3 minutes and makes an
uncredentialed call to a paid API from CI. That is exactly the regression these tests
exist to make loud. See tests/stub_llm.py for the measurements.
"""

import pytest

from app.conversation.llm import GeminiClient, get_llm_client
from tests.stub_llm import STUB_AUDIO, STUB_REPLY, STUB_TRANSCRIPT, StubGeminiClient


def test_factory_is_stubbed_by_default():
    """The autouse fixture must win, without the test asking for it."""
    assert isinstance(get_llm_client(), StubGeminiClient)


def test_stub_is_a_geminiclient():
    """conversation.py does `if not isinstance(llm, GeminiClient)` and skips tool-calling
    when False. If the stub ever stops being a GeminiClient, every conversation test
    silently switches to the plain-generate branch and the tool-calling loop goes
    untested — passing all the while. Pin the identity."""
    assert isinstance(get_llm_client(), GeminiClient)


def test_module_level_importers_are_all_patched():
    """Each module doing `from app.conversation.llm import get_llm_client` at module
    level binds its own name; the fixture sweeps sys.modules to catch them. If a new
    importer appears and the sweep misses it, this fails instead of CI slowing by 3min.
    """
    import importlib

    unpatched = []
    for name in (
        "app.api.v1.conversation",
        "app.notifications.briefing",
        "app.conversation",
    ):
        module = importlib.import_module(name)
        factory = getattr(module, "get_llm_client", None)
        if factory is None:
            continue
        if not isinstance(factory(), StubGeminiClient):
            unpatched.append(name)

    assert not unpatched, (
        f"module-level get_llm_client not stubbed in: {unpatched}. "
        "These will make real Vertex calls in CI."
    )


async def test_stub_generate_returns_canned_text():
    assert await get_llm_client().generate("sys", [{"role": "user", "content": "hi"}]) == STUB_REPLY


async def test_stub_tool_response_has_the_shape_conversation_py_reads():
    """conversation.py touches response.candidates[0].content.parts, part.function_call
    and response.text. Assert the duck-typed stub actually carries them — a stub that
    drifts from the real GenerationResponse would make tests pass against a shape
    production never returns."""
    response = await get_llm_client().generate_with_tools("sys", [])

    assert response.text == STUB_REPLY
    parts = response.candidates[0].content.parts
    assert parts and parts[0].function_call is None, "no function_call => the text path"


async def test_voice_backends_are_stubbed():
    """TTS was the LARGER half of the 184s (~12s per client construction, twice) and is
    the easiest to regress, because synthesize_speech returns None on failure and the
    caller treats None as a normal "no audio" result. Assert we get real bytes: that
    proves the stub is in force AND keeps the base64 path covered."""
    from app.api.v1.conversation import synthesize_speech, transcribe_audio

    assert await synthesize_speech("hello", "warm") == STUB_AUDIO
    assert await transcribe_audio(b"audio") == STUB_TRANSCRIPT


def test_gce_metadata_probe_is_disabled():
    """The backstop for a google.cloud client this suite does not know about yet.
    google-auth reads these at module-import time, so if conftest ever stops setting
    them early enough this silently reverts to ~12s per client."""
    import google.auth.compute_engine._metadata as metadata

    assert metadata._METADATA_DETECT_RETRIES == 0
    assert metadata._METADATA_DEFAULT_TIMEOUT == 1


@pytest.mark.real_llm
def test_real_llm_marker_opts_out(request):
    """The escape hatch works: this test gets the REAL factory, not the stub.

    It asserts the marker's wiring only — it makes no LLM call, so it stays offline."""
    assert isinstance(get_llm_client(), GeminiClient)
    assert not isinstance(get_llm_client(), StubGeminiClient)
