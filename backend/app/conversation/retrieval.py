"""Vector search — retrieve relevant document chunks for RAG."""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.embedding_client import embed_query

logger = logging.getLogger(__name__)


async def retrieve_relevant_chunks(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Embed query and retrieve the most relevant chunks.

    Returns list of dicts with chunk_text, source_field,
    classification, and similarity score.
    """
    query_embedding = await _embed_query(query)

    # pgvector cosine distance: <=> returns distance (0=identical)
    # similarity = 1 - distance
    sql = text("""
        SELECT
            dc.chunk_text,
            dc.source_field,
            d.classification,
            1 - (dc.embedding <=> :query_vec) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.user_id = :user_id
        ORDER BY dc.embedding <=> :query_vec
        LIMIT :top_k
    """)

    result = await db.execute(
        sql,
        {
            "query_vec": str(query_embedding),
            "user_id": str(user_id),
            "top_k": top_k,
        },
    )
    rows = result.fetchall()

    # chunk_text is encrypted per-user at rest; decrypt it (all rows belong to
    # user_id, which the query already scopes by) before it reaches the prompt.
    from app.services.field_crypto import decrypt_value

    # Filter by minimum similarity threshold
    chunks = []
    for row in rows:
        sim = float(row.similarity)
        if sim < 0.3:
            continue
        chunks.append({
            "chunk_text": await decrypt_value(db, user_id, row.chunk_text),
            "source_field": row.source_field,
            "classification": row.classification,
            "similarity": sim,
        })

    logger.info(
        "RAG: query returned %d chunks (of %d) for user %s",
        len(chunks),
        len(rows),
        user_id,
    )
    return chunks


async def _embed_query(query: str) -> list[float]:
    """Embed a query string using the local embedding model."""
    return await embed_query(query)
