"""Unit tests for pipeline/embedding_client.py — LiteLLM gateway embeddings."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.pipeline import embedding_client

# Exercises the embedding client itself, mocking its own network boundary
# (patches embedding_client._make_client), so the autouse stub in conftest would
# replace the very functions under test. Opt out — this module is already offline.
pytestmark = pytest.mark.real_ai


def _mock_client(vectors):
    """Mock AsyncOpenAI used as an async context manager.

    ``vectors`` is a list of embedding lists; returns (cm, client) where the
    client's embeddings.create is an AsyncMock yielding an OpenAI-shaped resp.
    """
    resp = SimpleNamespace(
        data=[SimpleNamespace(embedding=v, index=i) for i, v in enumerate(vectors)]
    )
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# _apply_prefix — nomic asymmetric retrieval prefixes
# ---------------------------------------------------------------------------


def test_apply_prefix_nomic_document_and_query():
    assert embedding_client._apply_prefix(["hi"], "document") == [
        "search_document: hi"
    ]
    assert embedding_client._apply_prefix(["hi"], "query") == ["search_query: hi"]


def test_apply_prefix_non_nomic_passthrough():
    with patch.object(settings, "embedding_model", "bge-m3"):
        assert embedding_client._apply_prefix(["hi"], "document") == ["hi"]


# ---------------------------------------------------------------------------
# embed_documents / embed_query
# ---------------------------------------------------------------------------


async def test_embed_documents_posts_prefixed_inputs():
    client = _mock_client([[0.1, 0.2], [0.3, 0.4]])
    with patch.object(embedding_client, "_make_client", return_value=client):
        out = await embedding_client.embed_documents(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    _, kwargs = client.embeddings.create.call_args
    assert kwargs["model"] == settings.embedding_model
    assert kwargs["input"] == ["search_document: a", "search_document: b"]


async def test_embed_query_returns_single_vector():
    client = _mock_client([[1.0, 2.0, 3.0]])
    with patch.object(embedding_client, "_make_client", return_value=client):
        out = await embedding_client.embed_query("hello")
    assert out == [1.0, 2.0, 3.0]


async def test_embed_reorders_by_index():
    """Out-of-order gateway data must be sorted back to input order."""
    resp = SimpleNamespace(
        data=[
            SimpleNamespace(embedding=[9.0], index=1),
            SimpleNamespace(embedding=[8.0], index=0),
        ]
    )
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch.object(embedding_client, "_make_client", return_value=client):
        out = await embedding_client.embed_documents(["a", "b"])
    assert out == [[8.0], [9.0]]


async def test_embed_empty_short_circuits():
    out = await embedding_client.embed_documents([])
    assert out == []


async def test_embed_count_mismatch_raises():
    client = _mock_client([[0.1]])  # 1 vec for 2 inputs
    with patch.object(embedding_client, "_make_client", return_value=client):
        with pytest.raises(ValueError):
            await embedding_client.embed_documents(["a", "b"])
