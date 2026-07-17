"""In-process stand-in for the Gemini/Vertex client, used by the whole test suite.

WHY THIS EXISTS
---------------
``settings.llm_provider`` defaults to ``"gemini"`` and CI sets no override, so before
this stub every test that reached ``/api/v1/conversation/*`` made a REAL Vertex call
from a GitHub-hosted runner. Measured: ``test_conversation_lifecycle`` took **184s** in
CI while its neighbours took ~0.3s. The runner has no GCP credentials, so those three
minutes were spent in credential discovery + retries, and the call then failed into
``_fallback_response``. The test asserts only status codes, so it passed either way —
the 184s bought no assertion at all, on an uncredentialed outbound call to a paid API.

WHY A GeminiClient SUBCLASS AND NOT A PLAIN FAKE
------------------------------------------------
``app/api/v1/conversation.py`` branches on the concrete type::

    if not isinstance(llm, GeminiClient):
        return await llm.generate(...)      # non-Gemini clients skip tool-calling

A generic stub would satisfy that check as False and silently route every test down the
plain-generate path — so the tool-calling loop (``generate_with_tools`` → function-call
dispatch → ``execute_tool``) would stop being exercised and nobody would notice. This
subclasses GeminiClient so the isinstance check stays True and tests keep covering the
SAME branch production takes. Only the network-touching methods are overridden.

WHAT IT IMPROVES
----------------
``generate_with_tools`` returns a duck-typed stand-in for the SDK's GenerationResponse
rather than ``None``. Returning None would reproduce today's behaviour exactly (straight
to ``_fallback_response``), which is fast but still covers nothing. By returning a
response with no function_call, tests now reach the real text path — including
``check_response_safety`` — which CI has never once executed.

The response shape is duck-typed against what the consumer actually touches:
``.candidates[0].content.parts``, ``part.function_call``, and ``.text``. It is NOT a real
``GenerationResponse``. If conversation.py starts reading another attribute, the stub
raises AttributeError loudly rather than degrading — that is deliberate: a silent
mismatch here would mean tests pass against a shape production never returns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.conversation.llm import GeminiClient

# Deterministic, obviously-synthetic values. Any test asserting on real model prose is
# asserting on a stub and should say so explicitly.
STUB_REPLY = "This is a stubbed D.D. reply for tests."

# TTS/STT stand-ins (used by the stub_ai_backends fixture). The TTS client is the LARGER
# half of the cost this file exists to remove: constructing TextToSpeechAsyncClient with
# no credentials burns ~12s probing the GCE metadata server, and synthesize_speech then
# swallows the failure and returns None — so it never looked like anything was wrong.
# Not real MP3: nothing under test decodes it, it only gets base64-encoded onto the
# response. If something ever does decode it, this should become a real fixture file.
STUB_AUDIO = b"stub-mp3-bytes"
STUB_TRANSCRIPT = "This is a stubbed transcript for tests."

# Embeddings. This one is NOT a google client — it is an openai.AsyncOpenAI pointed at
# settings.embedding_api_base, which defaults to the LiteLLM gateway on the LAN
# (192.168.0.104:4000) with a 60s timeout. From a CI runner that address is unroutable,
# so the SYN is blackholed and the call blocks for the FULL 60s. It is reached from the
# conversation path: prompt_builder._build_document_context -> retrieve_relevant_chunks
# -> embed_query, and prompt_builder swallows the failure and returns "" — so, again,
# nothing looked broken. This is invisible on a dev machine, where the gateway answers
# in milliseconds; only CI pays.
#
# Dimension must be 768 (document_chunk.embedding is Vector(768), nomic-embed-text) or
# pgvector rejects the comparison. Non-zero on purpose: cosine distance against a
# zero vector is undefined and would make the similarity query behave oddly rather
# than simply return no rows.
EMBEDDING_DIM = 768
STUB_EMBEDDING = [((i % 10) + 1) / 100.0 for i in range(EMBEDDING_DIM)]


class _StubPart:
    """One Part of a candidate's content. ``function_call = None`` == "model replied
    with text, no tool call" — the branch that reaches the safety layer."""

    def __init__(self, text: str = STUB_REPLY, function_call=None):
        self.text = text
        self.function_call = function_call


class _StubContent:
    def __init__(self, parts: list[_StubPart]):
        self.parts = parts
        self.role = "model"


class _StubCandidate:
    def __init__(self, parts: list[_StubPart]):
        self.content = _StubContent(parts)


class StubGenerationResponse:
    """Duck-typed stand-in for vertexai's GenerationResponse.

    Only the attributes conversation.py actually reads are implemented — see the module
    docstring on why that is intentional rather than lazy."""

    def __init__(self, text: str = STUB_REPLY, function_call=None):
        self._text = text
        self.candidates = [_StubCandidate([_StubPart(text, function_call)])]

    @property
    def text(self) -> str:
        return self._text


class StubGeminiClient(GeminiClient):
    """GeminiClient with every network path replaced. Inherits ``_fallback_response``
    and the isinstance identity; overrides nothing else."""

    def __init__(self, reply: str = STUB_REPLY):
        super().__init__()
        self.reply = reply
        # Records prompts so a test can assert what was sent to the model without a
        # network call. Not used by the suite yet; cheap and it makes the stub useful
        # for the prompt-builder / persona tests that would otherwise need Vertex.
        self.calls: list[dict] = []

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 500,
        temperature: float = 0.7,
        response_json: bool = False,
        disable_thinking: bool = False,
    ) -> str:
        self.calls.append({"kind": "generate", "system": system_prompt, "messages": messages})
        return self.reply

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 500,
        temperature: float = 0.7,
        disable_thinking: bool = False,
    ) -> AsyncIterator[str]:
        self.calls.append({"kind": "stream", "system": system_prompt, "messages": messages})
        # Multiple chunks: a single-chunk stream would not catch a consumer that
        # mishandles concatenation.
        for chunk in (self.reply[: len(self.reply) // 2], self.reply[len(self.reply) // 2 :]):
            yield chunk

    async def generate_with_tools(
        self,
        system_prompt: str,
        contents: list,
        tools=None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        disable_thinking: bool = False,
    ):
        self.calls.append({"kind": "tools", "system": system_prompt, "contents": contents})
        # No function_call -> the text path (incl. check_response_safety). A test that
        # wants the tool-dispatch loop should patch this method with its own response
        # carrying a function_call.
        return StubGenerationResponse(self.reply)
