import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)


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
    ) -> AsyncIterator[str]:
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
            if finish_name in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
                "SPII",
            }:
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
    ) -> AsyncIterator[str]:
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
            async for chunk in response:
                if chunk.text:
                    yield chunk.text
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
            return response
        except Exception:
            logger.exception(
                "Gemini tool-use call failed"
            )
            return None

    def _fallback_response(self, messages: list[dict]) -> str:
        last = messages[-1]["content"] if messages else ""
        return (
            f"I heard you say: \"{last[:100]}\". "
            "I'm having a little trouble connecting right now. "
            "Can you try again in a moment?"
        )


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
        last = messages[-1]["content"] if messages else ""
        return (
            f"I heard you say: \"{last[:100]}\". "
            "I'm having a little trouble connecting right now. "
            "Can you try again in a moment?"
        )


class OpenAIClient(LLMClient):
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
                self._client = openai.AsyncOpenAI(
                    api_key=settings.openai_api_key
                )
            except Exception:
                logger.warning("OpenAI client unavailable")
        return self._client

    async def generate(
        self, system_prompt: str, messages: list[dict], max_tokens: int = 500
    ) -> str:
        client = self._get_client()
        if client is None or not settings.openai_api_key:
            return self._fallback_response(messages)

        try:
            full_messages = [
                {"role": "system", "content": system_prompt}
            ] + messages
            response = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=max_tokens,
                messages=full_messages,
            )
            return response.choices[0].message.content
        except Exception:
            logger.exception("OpenAI API call failed")
            return self._fallback_response(messages)

    def _fallback_response(self, messages: list[dict]) -> str:
        last = messages[-1]["content"] if messages else ""
        return (
            f"I heard you say: \"{last[:100]}\". "
            "I'm having a little trouble right now. "
            "Can you try again?"
        )


def get_llm_client() -> LLMClient:
    """Get the configured LLM client."""
    if settings.llm_provider == "openai":
        return OpenAIClient()
    if settings.llm_provider == "anthropic":
        return ClaudeClient()
    return GeminiClient()
