"""Stage 1 — Ingestion: OCR (provider-abstracted), normalize into text."""

import difflib
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.pipeline.ocr import OcrResult, get_ocr_provider
from app.pipeline.schemas import NormalizedDocument
from app.services import storage_service
from app.services.field_crypto import encrypt_for_user

logger = logging.getLogger(__name__)

# Cap on PHI text stored in source_metadata (matches the ocr_text cap).
_OCR_TEXT_CAP = 5000


async def _ocr_pages(
    provider_name: str, page_datas: list[bytes], mime_type: str
) -> tuple[str, int]:
    """Run ``provider_name`` over one or more page images.

    Returns (concatenated_text, total_ms). For multi-page the per-page texts
    are concatenated with the same ``--- Page N ---`` framing the primary uses.
    """
    provider = get_ocr_provider(provider_name)
    if len(page_datas) > 1:
        results: list[OcrResult] = [
            await provider.extract_text(data, mime_type) for data in page_datas
        ]
        text = "\n\n".join(
            f"--- Page {i + 1} ---\n\n{r.text}"
            for i, r in enumerate(results)
        )
        return text, sum(r.ms for r in results)
    result = await provider.extract_text(page_datas[0], mime_type)
    return result.text, result.ms


async def _run_shadow_ocr(
    db: AsyncSession,
    doc: Document,
    *,
    primary_provider: str,
    primary_text: str,
    primary_ms: int,
    page_datas: list[bytes],
    mime_type: str,
) -> None:
    """Best-effort A/B comparison against the shadow engine.

    A shadow failure or timeout MUST NEVER affect the pipeline — every error is
    caught and logged. Records the comparison (with the shadow text encrypted
    at rest) on ``doc.source_metadata['ocr_shadow']``.
    """
    shadow_provider = settings.ocr_shadow_provider
    if not shadow_provider or shadow_provider == primary_provider:
        return
    try:
        shadow_text, shadow_ms = await _ocr_pages(
            shadow_provider, page_datas, mime_type
        )
        similarity = difflib.SequenceMatcher(
            None, primary_text, shadow_text
        ).ratio()
        enc_shadow = await encrypt_for_user(
            db, doc.user_id, shadow_text[:_OCR_TEXT_CAP]
        )
        if not doc.source_metadata:
            doc.source_metadata = {}
        doc.source_metadata["ocr_shadow"] = {
            "provider": shadow_provider,
            "primary_provider": primary_provider,
            "primary_chars": len(primary_text),
            "shadow_chars": len(shadow_text),
            "primary_ms": primary_ms,
            "shadow_ms": shadow_ms,
            "similarity": similarity,
            "shadow_text": enc_shadow,
        }
        await db.flush()
        logger.info(
            "OCR shadow %s vs %s: similarity=%.3f (%d vs %d chars)",
            shadow_provider, primary_provider, similarity,
            len(primary_text), len(shadow_text),
        )
    except Exception:
        # Shadow is purely observational — never let it break ingestion.
        logger.exception(
            "OCR shadow provider %s failed for document %s (ignored)",
            shadow_provider, doc.id,
        )


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
        primary_provider = settings.ocr_provider

        try:
            # Download all page images once; reused by primary + shadow.
            if len(page_refs) > 1:
                page_datas = []
                for i, ref in enumerate(page_refs):
                    logger.info("Downloading page %d from storage: %s", i, ref)
                    data = await storage_service.download(ref)
                    page_datas.append(data)
            else:
                logger.info("Downloading from storage: %s", doc.raw_text_ref)
                page_datas = [await storage_service.download(doc.raw_text_ref)]

            logger.info(
                "Running OCR (%s) on %d page(s) (%s)",
                primary_provider, len(page_datas), mime_type,
            )
            raw_text, primary_ms = await _ocr_pages(
                primary_provider, page_datas, mime_type
            )
            logger.info(
                "OCR extracted %d characters from %d page(s)",
                len(raw_text), len(page_datas),
            )

            # Store extracted text (keep original GCS path). PHI -> encrypt.
            if not doc.source_metadata:
                doc.source_metadata = {}
            doc.source_metadata["ocr_text"] = await encrypt_for_user(
                db, doc.user_id, raw_text[:_OCR_TEXT_CAP]
            )
            doc.source_metadata["ocr_complete"] = True
            await db.flush()

            # Shadow A/B comparison — best-effort, never affects the pipeline.
            await _run_shadow_ocr(
                db,
                doc,
                primary_provider=primary_provider,
                primary_text=raw_text,
                primary_ms=primary_ms,
                page_datas=page_datas,
                mime_type=mime_type,
            )
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
