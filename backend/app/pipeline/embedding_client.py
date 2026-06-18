"""Embedding client — local Ollama (nomic-embed-text), replaces Vertex AI.

Document and query embeddings MUST come from the same model, so both the
ingestion pipeline and chat retrieval go through this module.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _apply_prefix(texts: list[str], kind: str) -> list[str]:
    """nomic-embed-text expects task prefixes for asymmetric retrieval.

    ``kind`` is "document" or "query". Other models are passed through.
    """
    if settings.embedding_model.startswith("nomic"):
        return [f"search_{kind}: {t}" for t in texts]
    return texts


async def _embed(texts: list[str], kind: str) -> list[list[float]]:
    if not texts:
        return []
    payload = {
        "model": settings.embedding_model,
        "input": _apply_prefix(texts, kind),
    }
    async with httpx.AsyncClient(
        timeout=settings.embedding_timeout_seconds
    ) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/embed", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise ValueError(
            f"Ollama returned {len(embeddings or [])} embeddings "
            f"for {len(texts)} inputs"
        )
    return embeddings


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks for storage."""
    return await _embed(texts, "document")


async def embed_query(text: str) -> list[float]:
    """Embed a single query string for retrieval."""
    result = await _embed([text], "query")
    return result[0]
