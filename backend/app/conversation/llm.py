import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)

# Vertex finish_reasons that mean the model was BLOCKED / its output cut for
# content reasons. On these, any text produced is a partial/blocked fragment and
# must not be trusted as a complete answer. Shared by the non-streaming and
# streaming Gemini paths so both guard identically.
_BLOCKED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
)

# Member-facing fallback when a client can't produce a usable answer (model
# unavailable, blocked, or errored). It MUST NOT echo the member's input:
# dd-guidelines §8.5 explicitly forbids the "I heard you say: …" form, and
# parroting the member's words back at the moment content was blocked can repeat
# distressing input. A calm, bare retry prompt (§12.3). Single source of truth —
# the public reg-helper detects this exact string to swap in its own grounded
# refusal (see knowledge_service._is_unusable_answer_body).
LLM_FALLBACK_MESSAGE = (
    "I'm having trouble responding right now. Please try again in a moment."
)

# A streaming client normally yields plain text tokens (str). When a partial
# answer was already streamed but generation was then cut (content block or token
# budget), it yields ONE terminal structured chunk (dict) carrying this key, so
# the consumer can distinguish "the answer ended" from "the answer was cut off."
# `reason` is a coarse, non-sensitive category ("content" | "length") — never the
# raw provider finish_reason.
STREAM_CUT_SHORT_KEY = "cut_short"


def stream_cut_event(reason: str) -> dict:
    """Structured terminal stream chunk signalling a mid-answer cut (not a token)."""
    return {STREAM_CUT_SHORT_KEY: True, "reason": reason}


def cut_reason_for_finish(finish_name: str) -> str | None:
    """Map a Vertex finish_reason NAME to the coarse, non-sensitive cut category
    used by the member client ("content" for a block, "length" for a token-budget
    cut), or None when the generation finished normally. Same category surface as
    the streaming ``stream_cut_event`` — never exposes the raw finish_reason."""
    if finish_name in _BLOCKED_FINISH_REASONS:
        return "content"
    if finish_name == "MAX_TOKENS":
        return "length"
    return None


def extract_json(text: str) -> dict:
    """Extract a JSON object from LLM output that may contain
    preamble text, markdown fences, or thinking blocks."""
    cleaned = text.strip()
    # Strip markdown code fences
    if "```" in cleaned:
        cleaned = re.sub(
            r"^```(?:json)?\s*", "", cleaned
        )
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find the first { ... } block
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start : i + 1])
    # Fallback: try parsing the whole thing
    return json.loads(cleaned)


class LLMClient(ABC):
    @abstractmethod
    async def generate(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 500
    ) -> str:
        ...

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> AsyncIterator[str | dict]:
        """Yield text chunks. Default: single chunk fallback."""
        text = await self.generate(
            system_prompt, messages, max_tokens
        )
        yield text


class GeminiClient(LLMClient):
    """Gemini via Vertex AI. Uses service account auth (no API key needed)."""

    def __init__(self):
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            try:
                import vertexai
                vertexai.init(
                    project=settings.gcp_project_id,
                    location=settings.gemini_location,
                )
                self._initialized = True
            except Exception:
                logger.exception("Vertex AI init failed")

    def _get_model(
        self, system_prompt: str = "", tools=None
    ):
        self._ensure_init()
        if not self._initialized:
            return None
        try:
            from vertexai.generative_models import (
                GenerativeModel,
            )
            kwargs = {
                "model_name": settings.gemini_model,
                "system_instruction": system_prompt,
            }
            if tools is not None:
                kwargs["tools"] = (
                    tools
                    if isinstance(tools, list)
                    else [tools]
                )
            return GenerativeModel(**kwargs)
        except Exception:
            logger.exception("Gemini model init failed")
            return None

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 500,
        temperature: float = 0.7,
        response_json: bool = False,
        disable_thinking: bool = False,
    ) -> str:
        model = self._get_model(system_prompt)
        if model is None:
            return self._fallback_response(messages)

        try:
            from vertexai.generative_models import (
                Content,
                GenerationConfig,
                Part,
            )

            contents = []
            for msg in messages:
                role = (
                    "user"
                    if msg["role"] == "user"
                    else "model"
                )
                contents.append(
                    Content(
                        role=role,
                        parts=[Part.from_text(msg["content"])],
                    )
                )

            gen_kwargs = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if response_json:
                gen_kwargs["response_mime_type"] = "application/json"
            
            if disable_thinking:
                # ThinkingConfig was removed from vertexai.generative_models in
                # newer SDK versions; if unavailable, skip it — thinking stays on
                # and any thinking blocks are stripped from the output downstream.
                try:
                    from vertexai.generative_models import ThinkingConfig
                    gen_kwargs["thinking_config"] = ThinkingConfig(thinking_budget=0)
                except ImportError:
                    pass

            response = await model.generate_content_async(
                contents,
                generation_config=GenerationConfig(**gen_kwargs),
            )
            # Inspect finish_reason BEFORE trusting the text. Vertex silently
            # returns whatever partial text it produced when a generation is cut
            # short, so `response.text` on a truncated/blocked response yields a
            # mid-sentence fragment with no error.
            candidate = response.candidates[0] if response.candidates else None
            finish_name = getattr(
                getattr(candidate, "finish_reason", None), "name", ""
            )
            # Content-blocked / recitation-cut responses must NOT be served as a
            # partial fragment — fall back cleanly instead.
            if finish_name in _BLOCKED_FINISH_REASONS:
                logger.warning(
                    "Gemini generation terminated by %s; returning fallback "
                    "instead of a partial response.",
                    finish_name,
                )
                return self._fallback_response(messages)
            # MAX_TOKENS => the answer was cut at the token budget. With the
            # budgets callers now pass this should be rare; log it so a recurrence
            # is visible (the fix is a larger budget or disabled thinking).
            if finish_name == "MAX_TOKENS":
                logger.warning(
                    "Gemini generation hit MAX_TOKENS (max_output_tokens=%s); the "
                    "answer may be truncated. Raise the budget or disable thinking.",
                    max_tokens,
                )
            # Try response.text first, fall back to extracting
            # from candidates if the model returned thinking
            # tokens but no direct text
            try:
                return response.text
            except ValueError:
                # Try to get text from candidate parts
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text
                logger.warning(
                    "Gemini returned no text content. "
                    "Candidates: %s",
                    len(response.candidates)
                    if response.candidates
                    else 0,
                )
                return self._fallback_response(messages)
        except Exception:
            logger.exception("Gemini API call failed")
            return self._fallback_response(messages)

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 500,
        temperature: float = 0.7,
        disable_thinking: bool = False,
    ) -> AsyncIterator[str | dict]:
        model = self._get_model(system_prompt)
        if model is None:
            yield self._fallback_response(messages)
            return

        try:
            from vertexai.generative_models import (
                Content,
                GenerationConfig,
                Part,
            )

            contents = []
            for msg in messages:
                role = (
                    "user" if msg["role"] == "user" else "model"
                )
                contents.append(
                    Content(
                        role=role,
                        parts=[Part.from_text(msg["content"])],
                    )
                )

            gen_kwargs = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if disable_thinking:
                # ThinkingConfig was removed from vertexai.generative_models in
                # newer SDK versions; if unavailable, skip it — thinking stays on
                # and any thinking blocks are stripped from the output downstream.
                try:
                    from vertexai.generative_models import ThinkingConfig
                    gen_kwargs["thinking_config"] = ThinkingConfig(thinking_budget=0)
                except ImportError:
                    pass

            response = await model.generate_content_async(
                contents,
                stream=True,
                generation_config=GenerationConfig(**gen_kwargs),
            )
            # Track finish_reason as it arrives (the terminal chunk carries it).
            # Streamed text can't be retracted, but a blocked stream that emitted
            # NOTHING should still yield a graceful fallback rather than silence,
            # and every blocked/truncated cut is logged for parity with generate().
            emitted_any = False
            finish_name = ""
            async for chunk in response:
                candidate = (
                    chunk.candidates[0]
                    if getattr(chunk, "candidates", None)
                    else None
                )
                name = getattr(
                    getattr(candidate, "finish_reason", None), "name", ""
                )
                if name:
                    finish_name = name
                try:
                    text = chunk.text
                except (ValueError, IndexError):
                    text = ""
                if text:
                    emitted_any = True
                    yield text
            if finish_name in _BLOCKED_FINISH_REASONS:
                logger.warning(
                    "Gemini stream terminated by %s (emitted_any=%s).",
                    finish_name,
                    emitted_any,
                )
                if not emitted_any:
                    yield self._fallback_response(messages)
                else:
                    # A partial answer already reached the member and can't be
                    # retracted — emit a terminal cut-short signal so the client
                    # can render a soft "response stopped early" note instead of
                    # leaving the member with an unexplained mid-sentence stop.
                    yield stream_cut_event("content")
            elif finish_name == "MAX_TOKENS":
                logger.warning(
                    "Gemini stream hit MAX_TOKENS (max_output_tokens=%s); the "
                    "response may be truncated.",
                    max_tokens,
                )
                if emitted_any:
                    yield stream_cut_event("length")
        except Exception:
            logger.exception("Gemini streaming failed")
            yield self._fallback_response(messages)

    async def generate_with_tools(
        self,
        system_prompt: str,
        contents: list,
        tools=None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        disable_thinking: bool = False,
    ):
        """Generate with tool support.

        Accepts Content objects directly and returns
        the full GenerationResponse.
        """
        model = self._get_model(system_prompt, tools=tools)
        if model is None:
            return None

        try:
            from vertexai.generative_models import (
                GenerationConfig,
            )

            gen_kwargs = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if disable_thinking:
                # ThinkingConfig was removed from vertexai.generative_models in
                # newer SDK versions; if unavailable, skip it — thinking stays on
                # and any thinking blocks are stripped from the output downstream.
                try:
                    from vertexai.generative_models import ThinkingConfig
                    gen_kwargs["thinking_config"] = ThinkingConfig(thinking_budget=0)
                except ImportError:
                    pass

            response = await model.generate_content_async(
                contents,
                generation_config=GenerationConfig(**gen_kwargs),
            )
            # Surface a blocked/recitation cut for observability. The caller owns
            # the response (it may carry tool calls), so we don't alter it here —
            # but a content-block should not pass silently.
            candidate = (
                response.candidates[0]
                if getattr(response, "candidates", None)
                else None
            )
            finish_name = getattr(
                getattr(candidate, "finish_reason", None), "name", ""
            )
            if finish_name in _BLOCKED_FINISH_REASONS:
                logger.warning(
                    "Gemini tool-use generation terminated by %s.", finish_name
                )
            return response
        except Exception:
            logger.exception(
                "Gemini tool-use call failed"
            )
            return None

    def _fallback_response(self, messages: list[dict]) -> str:
        # §8.5-compliant: a calm retry that never echoes the member's input.
        return LLM_FALLBACK_MESSAGE


class ClaudeClient(LLMClient):
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(
                    api_key=settings.anthropic_api_key
                )
            except Exception:
                logger.warning("Anthropic client unavailable")
        return self._client

    async def generate(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 500
    ) -> str:
        client = self._get_client()
        if client is None or not settings.anthropic_api_key:
            return self._fallback_response(messages)

        try:
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
            return response.content[0].text
        except Exception:
            logger.exception("Claude API call failed")
            return self._fallback_response(messages)

    def _fallback_response(self, messages: list[dict]) -> str:
        # §8.5-compliant: a calm retry that never echoes the member's input.
        return LLM_FALLBACK_MESSAGE


class OpenAIClient(LLMClient):
    """OpenAI-compatible chat client.

    Generalized to accept a ``base_url``, ``model`` and ``api_key`` so it can
    front either the hosted OpenAI API (defaults: no base_url, ``gpt-4o``,
    ``settings.openai_api_key``) OR any OpenAI-compatible gateway (e.g. the
    self-hosted LiteLLM gateway fronting qwen2.5 — see :class:`GatewayLLMClient`).
    All args are keyword-only with the historical defaults, so the existing
    member-path construction ``OpenAIClient()`` is unchanged.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str = "gpt-4o",
        api_key: str | None = None,
    ):
        self._client = None
        self._base_url = base_url
        self._model = model
        self._api_key = (
            api_key if api_key is not None else settings.openai_api_key
        )

    def _get_client(self):
        if self._client is None:
            try:
                import openai
                kwargs = {
                    # LiteLLM/OpenAI reject empty keys; a placeholder keeps the
                    # SDK constructable in local/test envs (mirrors embedding_client).
                    "api_key": self._api_key or "missing",
                }
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = openai.AsyncOpenAI(**kwargs)
            except Exception:
                logger.warning("OpenAI client unavailable")
        return self._client

    async def generate(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 500
    ) -> str:
        client = self._get_client()
        if client is None or not self._api_key:
            return self._fallback_response(messages)

        try:
            full_messages = [
                {"role": "system", "content": system_prompt}
            ] + messages
            response = await client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=full_messages,
            )
            choice = response.choices[0]
            # Mirror the GeminiClient finish_reason guard: an OpenAI-compatible
            # provider returns finish_reason="length" when the answer was cut at
            # the token budget. qwen2.5 is NOT a thinking model, so max_tokens
            # (3072 for the reg-helper) is ample and this should be rare — but
            # log it so a recurrence is visible instead of silently serving a
            # mid-sentence fragment. We still return the (partial) content; the
            # answer contract stitches provenance + disclaimer around it in code.
            if getattr(choice, "finish_reason", None) == "length":
                logger.warning(
                    "OpenAI-compatible generation hit finish_reason=length "
                    "(model=%s, max_tokens=%s); the answer may be truncated. "
                    "Raise the budget.",
                    self._model,
                    max_tokens,
                )
            return choice.message.content
        except Exception:
            logger.exception("OpenAI API call failed")
            return self._fallback_response(messages)

    def _fallback_response(self, messages: list[dict]) -> str:
        # §8.5-compliant: a calm retry that never echoes the member's input.
        return LLM_FALLBACK_MESSAGE


class GatewayLLMClient(OpenAIClient):
    """OpenAI-compatible client pointed at the self-hosted LiteLLM gateway.

    Used ONLY by the PUBLIC disability-benefits RAG surface (generate_rag_answer)
    — a no-PHI, grounded federal-regulation helper. It NEVER serves the member
    D.D./Arlo PHI assistant, which stays on Gemini via ``get_llm_client``.

    Reads the knowledge-scoped config (base_url/model/key), all resolving to the
    shared embedding gateway defaults when unset, so grounded summarization can
    run on self-hosted qwen2.5 to save token cost.
    """

    def __init__(self):
        super().__init__(
            base_url=settings.knowledge_llm_api_base_resolved,
            model=settings.knowledge_llm_model,
            api_key=settings.knowledge_llm_api_key_resolved or "missing",
        )


def get_llm_client() -> LLMClient:
    """Get the configured LLM client for the member D.D./Arlo assistant path.

    Dispatches on the GLOBAL ``settings.llm_provider``. This is the PHI member
    path and stays on Gemini in prod. Do NOT route the public reg-helper through
    here — use :func:`get_knowledge_llm_client`.
    """
    if settings.llm_provider == "openai":
        return OpenAIClient()
    if settings.llm_provider == "anthropic":
        return ClaudeClient()
    return GeminiClient()


def get_knowledge_llm_client() -> LLMClient:
    """Get the LLM client for the PUBLIC disability-benefits RAG surface ONLY.

    Reads the knowledge-scoped ``settings.knowledge_llm_provider`` flag, which is
    independent of the global ``settings.llm_provider`` that drives the member
    PHI assistant. Defaults to Gemini so nothing changes until the flag is
    flipped; "qwen" (alias "gateway") routes grounded summarization to
    self-hosted qwen2.5 via the LiteLLM gateway. This selector is NEVER used by
    the member path.
    """
    if settings.knowledge_llm_provider in ("qwen", "gateway"):
        return GatewayLLMClient()
    return GeminiClient()
