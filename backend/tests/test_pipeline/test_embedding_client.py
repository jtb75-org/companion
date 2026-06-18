"""Unit tests for pipeline/embedding_client.py — Ollama embeddings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.pipeline import embedding_client


def _mock_client(json_payload):
    """Build a mock httpx.AsyncClient context manager + captured client."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_payload)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# _apply_prefix — nomic asymmetric retrieval prefixes
# ---------------------------------------------------------------------------


def test_apply_prefix_nomic_document_and_query():
    assert embedding_client._apply_prefix(["hi"], "document") == [
        "search_document: hi"
    ]
    assert embedding_client._apply_prefix(["hi"], "query") == [
        "search_query: hi"
    ]


def test_apply_prefix_non_nomic_passthrough():
    with patch.object(settings, "embedding_model", "bge-m3"):
        assert embedding_client._apply_prefix(["hi"], "document") == ["hi"]


# ---------------------------------------------------------------------------
# embed_documents / embed_query
# ---------------------------------------------------------------------------


async def test_embed_documents_posts_prefixed_inputs():
    cm, client = _mock_client({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    with patch.object(embedding_client.httpx, "AsyncClient", return_value=cm):
        out = await embedding_client.embed_documents(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    _, kwargs = client.post.call_args
    assert kwargs["json"]["model"] == settings.embedding_model
    assert kwargs["json"]["input"] == ["search_document: a", "search_document: b"]


async def test_embed_query_returns_single_vector():
    cm, _ = _mock_client({"embeddings": [[1.0, 2.0, 3.0]]})
    with patch.object(embedding_client.httpx, "AsyncClient", return_value=cm):
        out = await embedding_client.embed_query("hello")
    assert out == [1.0, 2.0, 3.0]


async def test_embed_empty_short_circuits():
    out = await embedding_client.embed_documents([])
    assert out == []


async def test_embed_count_mismatch_raises():
    cm, _ = _mock_client({"embeddings": [[0.1]]})  # 1 vec for 2 inputs
    with patch.object(embedding_client.httpx, "AsyncClient", return_value=cm):
        with pytest.raises(ValueError):
            await embedding_client.embed_documents(["a", "b"])
