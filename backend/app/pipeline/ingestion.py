"""Stage 1 — Ingestion: OCR via Document AI, normalize into text."""

import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.pipeline.schemas import NormalizedDocument
from app.services import storage_service

logger = logging.getLogger(__name__)


def _ocr_with_document_ai(
    image_data: bytes, mime_type: str
) -> str:
    """Run OCR using Google Document AI. Synchronous."""
    from google.cloud import documentai_v1 as documentai

    client = documentai.DocumentProcessorServiceClient()
    resource_name = client.processor_path(
        settings.gcp_project_id,
        settings.documentai_location,
        settings.documentai_processor_id,
    )

    raw_document = documentai.RawDocument(
        content=image_data, mime_type=mime_type
    )
    request = documentai.ProcessRequest(
        name=resource_name, raw_document=raw_document
    )

    result = client.process_document(request=request)
    return result.document.text


async def process_camera_scan(
    db: AsyncSession,
    document_id: UUID,
    image_data: bytes | None = None,
) -> NormalizedDocument:
    """Process a camera scan: download from GCS, OCR, normalize."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    raw_text = ""
    mime_type = "image/jpeg"

    # Get mime type from metadata
    if doc.source_metadata and isinstance(
        doc.source_metadata, dict
    ):
        mime_type = doc.source_metadata.get(
            "content_type", mime_type
        )
        # Check for pre-provided raw text (tests)
        raw_text = doc.source_metadata.get("raw_text", "")

    if not raw_text and doc.raw_text_ref:
        # Check for multi-page scan
        page_refs = (doc.source_metadata or {}).get("page_refs", [])

        try:
            if len(page_refs) > 1:
                # Multi-page: OCR each page and concatenate
                page_texts = []
                for i, ref in enumerate(page_refs):
                    logger.info("Downloading page %d from storage: %s", i, ref)
                    data = await storage_service.download(ref)
                    logger.info("Running OCR on page %d (%d bytes)", i, len(data))
                    text = await asyncio.to_thread(_ocr_with_document_ai, data, mime_type)
                    page_texts.append(text)
                raw_text = "\n\n".join(
                    f"--- Page {i + 1} ---\n\n{text}"
                    for i, text in enumerate(page_texts)
                )
                logger.info(
                    "OCR extracted %d characters from %d pages",
                    len(raw_text), len(page_refs),
                )
            else:
                # Single page (or legacy): download and OCR
                logger.info("Downloading from storage: %s", doc.raw_text_ref)
                data = await storage_service.download(doc.raw_text_ref)
                logger.info("Running OCR on %d bytes (%s)", len(data), mime_type)
                raw_text = await asyncio.to_thread(_ocr_with_document_ai, data, mime_type)
                logger.info("OCR extracted %d characters", len(raw_text))

            # Store extracted text (keep original GCS path)
            if not doc.source_metadata:
                doc.source_metadata = {}
            doc.source_metadata["ocr_text"] = raw_text[:5000]
            doc.source_metadata["ocr_complete"] = True
            await db.flush()
        except Exception:
            logger.exception(
                "OCR failed for document %s", document_id
            )
            raw_text = "[OCR failed - image could not be read]"

    quality_score = 0.85 if raw_text else 0.0

    return NormalizedDocument(
        document_id=document_id,
        user_id=doc.user_id,
        source_channel=getattr(
            doc.source_channel, "value", str(doc.source_channel)
        ),
        raw_text=raw_text,
        metadata=doc.source_metadata or {},
        quality_score=quality_score,
    )


async def process_email(
    db: AsyncSession,
    document_id: UUID,
    email_content: dict | None = None,
) -> NormalizedDocument:
    """Process an email into normalized text."""
    doc = await db.get(Document, document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    raw_text = ""
    if email_content:
        raw_text = email_content.get("body_text", "")
    elif doc.source_metadata and isinstance(
        doc.source_metadata, dict
    ):
        raw_text = doc.source_metadata.get(
            "body_text",
            doc.source_metadata.get("raw_text", ""),
        )

    return NormalizedDocument(
        document_id=document_id,
        user_id=doc.user_id,
        source_channel="email",
        raw_text=raw_text,
        metadata=doc.source_metadata or {},
        quality_score=1.0,
    )
