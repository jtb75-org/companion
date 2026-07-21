"""Knowledge-scoped LLM selector + gateway client, and the reg-helper answer contract.

Covers the ENV-flagged ability to run the PUBLIC disability-benefits RAG generation
(generate_rag_answer) on self-hosted qwen2.5 via the LiteLLM gateway, keeping Gemini the
default and the member D.D./Arlo PHI path untouched. All tests are hermetic — no network,
no live gateway/Vertex call.
"""

from datetime import datetime

import pytest

from app import config as config_module
from app.conversation.llm import (
    GatewayLLMClient,
    GeminiClient,
    OpenAIClient,
    get_knowledge_llm_client,
    get_llm_client,
)
from app.services import knowledge_service

# ── Selector: knowledge flag picks the right client, member path unaffected ──────
#
# The real_ai marker opts these OUT of the autouse stub, which otherwise replaces
# get_knowledge_llm_client with the StubGeminiClient — we want the REAL selector here.
# No network is touched: we only construct clients and assert their type/attributes.


@pytest.mark.real_ai
def test_selector_defaults_to_gemini(monkeypatch):
    """Default flag = gemini → the reg-helper generates on Gemini (nothing changes until
    the flag is flipped)."""
    monkeypatch.setattr(config_module.settings, "knowledge_llm_provider", "gemini")
    client = get_knowledge_llm_client()
    assert isinstance(client, GeminiClient)
    assert not isinstance(client, GatewayLLMClient)


@pytest.mark.real_ai
@pytest.mark.parametrize("flag", ["qwen", "gateway"])
def test_selector_routes_to_gateway_when_flipped(monkeypatch, flag):
    """qwen (and its "gateway" alias) → the OpenAI-compatible GatewayLLMClient."""
    monkeypatch.setattr(config_module.settings, "knowledge_llm_provider", flag)
    client = get_knowledge_llm_client()
    assert isinstance(client, GatewayLLMClient)
    # It IS an OpenAIClient subclass (the generalized OpenAI-compatible path).
    assert isinstance(client, OpenAIClient)


@pytest.mark.real_ai
def test_member_path_ignores_knowledge_flag(monkeypatch):
    """Flipping the KNOWLEDGE flag must never move the member D.D./Arlo path off Gemini —
    that dispatches on the GLOBAL llm_provider, not the knowledge flag."""
    monkeypatch.setattr(config_module.settings, "knowledge_llm_provider", "qwen")
    monkeypatch.setattr(config_module.settings, "llm_provider", "gemini")
    member = get_llm_client()
    assert isinstance(member, GeminiClient)
    assert not isinstance(member, GatewayLLMClient)


# ── Gateway client construction: base_url / model / key come from config ─────────


@pytest.mark.real_ai
def test_gateway_client_constructed_with_configured_values(monkeypatch):
    """The gateway client carries the configured base_url, model and key."""
    monkeypatch.setattr(
        config_module.settings, "knowledge_llm_api_base", "http://gw.example:4000/v1"
    )
    monkeypatch.setattr(config_module.settings, "knowledge_llm_model", "qwen2.5:32b")
    monkeypatch.setattr(config_module.settings, "knowledge_llm_api_key", "sk-knowledge")

    client = GatewayLLMClient()
    assert client._base_url == "http://gw.example:4000/v1"
    assert client._model == "qwen2.5:32b"
    assert client._api_key == "sk-knowledge"


@pytest.mark.real_ai
def test_gateway_client_falls_back_to_embedding_gateway_defaults(monkeypatch):
    """Unset knowledge base/key → reuse the shared embedding gateway base/key, so the
    reg-helper rides the same in-cluster LiteLLM gateway the embeddings already use."""
    monkeypatch.setattr(config_module.settings, "knowledge_llm_api_base", "")
    monkeypatch.setattr(config_module.settings, "knowledge_llm_api_key", "")
    monkeypatch.setattr(
        config_module.settings, "embedding_api_base", "http://embed-gw:4000/v1"
    )
    monkeypatch.setattr(config_module.settings, "embedding_api_key", "sk-embed")

    client = GatewayLLMClient()
    assert client._base_url == "http://embed-gw:4000/v1"
    assert client._api_key == "sk-embed"


@pytest.mark.real_ai
def test_gateway_client_defaults_to_qwen_14b_model(monkeypatch):
    """The default configured model is a concrete pulled model (qwen2.5:14b)."""
    # Reload-free: just read the field default off a fresh Settings-derived value.
    assert config_module.Settings().knowledge_llm_model == "qwen2.5:14b"


# ── Gateway client generate(): uses the configured model, handles finish_reason ──


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, content, finish_reason):
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeOpenAI:
    """Records the create() kwargs so a test can assert the model routed through."""

    def __init__(self, content="ok", finish_reason="stop"):
        self._content = content
        self._finish_reason = finish_reason
        self.create_kwargs = None

        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.create_kwargs = kwargs
                return _FakeCompletion(outer._content, outer._finish_reason)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


@pytest.mark.real_ai
async def test_gateway_generate_uses_configured_model(monkeypatch):
    monkeypatch.setattr(config_module.settings, "knowledge_llm_model", "qwen2.5:14b")
    monkeypatch.setattr(config_module.settings, "knowledge_llm_api_key", "sk-x")
    client = GatewayLLMClient()
    fake = _FakeOpenAI(content="a grounded answer")
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    out = await client.generate("sys", [{"role": "user", "content": "hi"}], max_tokens=3072)

    assert out == "a grounded answer"
    assert fake.create_kwargs["model"] == "qwen2.5:14b"
    assert fake.create_kwargs["max_tokens"] == 3072
    # System prompt is prepended as a system message.
    assert fake.create_kwargs["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.real_ai
async def test_gateway_generate_logs_but_returns_on_length_cut(monkeypatch, caplog):
    """finish_reason=length (a token-budget cut) is logged, not crashed, and the partial
    content is still returned (the answer contract re-wraps it in code)."""
    monkeypatch.setattr(config_module.settings, "knowledge_llm_api_key", "sk-x")
    client = GatewayLLMClient()
    fake = _FakeOpenAI(content="partial answer", finish_reason="length")
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    with caplog.at_level("WARNING"):
        out = await client.generate("sys", [{"role": "user", "content": "hi"}])

    assert out == "partial answer"
    assert any("finish_reason=length" in r.message for r in caplog.records)


# ── Answer contract holds around a mocked gateway response ───────────────────────


def _fake_chunk():
    return {
        "id": "chunk-1",
        "jurisdiction": "US_Federal",
        "source_corpus": "eCFR",
        "source_url": "https://www.ecfr.gov/current/title-20/part-404",
        "citation": "20 CFR § 404.1520",
        "program": "SSDI",
        "text_content": "We use a five-step sequential evaluation process.",
        "effective_date": datetime(2026, 1, 1),
        "similarity": 0.91,
    }


def _patch_search(monkeypatch, chunks):
    async def _fake_search(db, query_text, program_filter=None, limit=5):
        return chunks

    monkeypatch.setattr(knowledge_service, "search_regulations", _fake_search)


class _FakeKnowledgeClient:
    def __init__(self, reply):
        self.reply = reply

    async def generate(self, system_prompt, messages, max_tokens=500):
        return self.reply


async def test_answer_contract_wraps_gateway_response(monkeypatch):
    """A mocked gateway body (no disclaimer, no provenance) still emerges from
    generate_rag_answer with the not-legal-advice disclaimer, provenance line and the
    structural citation — proving the contract is enforced in code, model-agnostically."""
    _patch_search(monkeypatch, [_fake_chunk()])
    monkeypatch.setattr(
        knowledge_service,
        "get_knowledge_llm_client",
        lambda: _FakeKnowledgeClient("The process has five steps (20 CFR § 404.1520)."),
    )

    result = await knowledge_service.generate_rag_answer(None, "how does the five-step work")

    assert result["grounded"] is True
    assert knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER in result["answer"]
    assert result["answer"].startswith("Provenance: As of ")
    assert "five steps" in result["answer"]
    assert "20 CFR § 404.1520" in result["citations"]


@pytest.mark.parametrize(
    "bad_body",
    [
        "",
        '\n  ',
        'I heard you say: "how does this work". '
        "I'm having a little trouble connecting right now. Can you try again in a moment?",
        'I heard you say: "how does this work". '
        "I'm having a little trouble right now. Can you try again?",
    ],
)
async def test_failed_generation_degrades_to_grounded_refusal(monkeypatch, bad_body):
    """A blocked/empty/gateway-error body (empty or the shared conversational fallback)
    must NOT be served on this legal surface. It degrades to the deterministic grounded
    refusal — never the "I heard you say" copy — while still carrying provenance,
    disclaimer and the structural citation. (Folds in safety follow-up #1.)"""
    _patch_search(monkeypatch, [_fake_chunk()])
    monkeypatch.setattr(
        knowledge_service,
        "get_knowledge_llm_client",
        lambda: _FakeKnowledgeClient(bad_body),
    )

    result = await knowledge_service.generate_rag_answer(None, "how does this work")

    assert result["grounded"] is False
    assert "I heard you say" not in result["answer"]
    assert "cannot find the answer" in result["answer"].lower()
    assert knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER in result["answer"]
    assert result["answer"].startswith("Provenance: As of ")
    # Structural citations still ship — the chunks were genuinely retrieved.
    assert "20 CFR § 404.1520" in result["citations"]
