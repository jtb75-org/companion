"""Embedding client — via the shared LiteLLM gateway (OpenAI-compatible).

LiteLLM fronts nomic-embed-text on both Mac Studios and load-balances /
fails over between them, so this is the embedding HA layer. Document and
query embeddings MUST come from the same model, so both the ingestion
pipeline and chat retrieval go through this module.
"""

import logging

import openai

from app.config import settings

logger = logging.getLogger(__name__)


def _apply_prefix(texts: list[str], kind: str) -> list[str]:
    """nomic-embed-text expects task prefixes for asymmetric retrieval.

    ``kind`` is "document" or "query". Other models are passed through.
    """
    if settings.embedding_model.startswith("nomic"):
        return [f"search_{kind}: {t}" for t in texts]
    return texts


def _make_client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=settings.embedding_api_base,
        # LiteLLM rejects empty keys; placeholder keeps the SDK constructable
        # in local/test envs where no gateway key is configured.
        api_key=settings.embedding_api_key or "missing",
        timeout=settings.embedding_timeout_seconds,
    )


async def _embed(texts: list[str], kind: str) -> list[list[float]]:
    if not texts:
        return []
    async with _make_client() as client:
        resp = await client.embeddings.create(
            model=settings.embedding_model,
            input=_apply_prefix(texts, kind),
        )
    # OpenAI returns data ordered by input index; sort defensively.
    items = sorted(resp.data, key=lambda d: getattr(d, "index", 0))
    if len(items) != len(texts):
        raise ValueError(
            f"Gateway returned {len(items)} embeddings for {len(texts)} inputs"
        )
    return [d.embedding for d in items]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks for storage."""
    return await _embed(texts, "document")


async def embed_query(text: str) -> list[float]:
    """Embed a single query string for retrieval."""
    result = await _embed([text], "query")
    return result[0]
