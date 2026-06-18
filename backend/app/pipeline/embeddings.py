"""Embedding service — generates vector embeddings for document chunks."""

import logging
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.pipeline.chunking import chunk_document
from app.pipeline.embedding_client import embed_documents
from app.pipeline.schemas import (
    ClassificationResult,
    ExtractionResult,
    SummarizationResult,
)

logger = logging.getLogger(__name__)


async def embed_document(
    db: AsyncSession,
    document_id: UUID,
    user_id: UUID,
    classification_result: ClassificationResult,
    extraction_result: ExtractionResult,
    summarization_result: SummarizationResult,
) -> int:
    """Generate embeddings for a document's chunks.

    Returns the number of chunks embedded.
    """
    # Get OCR text from the document's source_metadata
    doc = await db.get(Document, document_id)
    ocr_text = ""
    if doc and doc.source_metadata:
        ocr_text = doc.source_metadata.get("ocr_text", "")
    if not ocr_text:
        ocr_text = extraction_result.extracted_fields.get(
            "raw_text", ""
        )

    # Build chunks from pipeline results
    chunks = chunk_document(
        classification=classification_result.classification,
        ocr_text=ocr_text,
        spoken_summary=summarization_result.spoken_summary,
        card_summary=summarization_result.card_summary,
        extracted_fields=extraction_result.extracted_fields,
    )

    if not chunks:
        logger.info(
            "No chunks to embed for document %s", document_id
        )
        return 0

    # Delete existing chunks for re-embedding support
    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.document_id == document_id
        )
    )

    # Get embeddings from local Ollama (nomic-embed-text)
    texts = [c["chunk_text"] for c in chunks]
    embeddings = await embed_documents(texts)

    # Insert chunk rows
    for chunk_data, embedding in zip(
        chunks, embeddings, strict=True
    ):
        chunk = DocumentChunk(
            document_id=document_id,
            user_id=user_id,
            chunk_index=chunk_data["chunk_index"],
            chunk_text=chunk_data["chunk_text"],
            token_count=len(chunk_data["chunk_text"]) // 4,
            source_field=chunk_data["source_field"],
            embedding=embedding,
        )
        db.add(chunk)

    await db.flush()
    logger.info(
        "Embedded %d chunks for document %s",
        len(chunks),
        document_id,
    )
    return len(chunks)
