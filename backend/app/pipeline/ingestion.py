"""Stage 1 — Ingestion: OCR (provider-abstracted), normalize into text."""

import difflib
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.enums import ConfigCategory
from app.pipeline.ocr import OcrResult, get_ocr_provider
from app.pipeline.schemas import NormalizedDocument
from app.services import config_service, storage_service
from app.services.field_crypto import encrypt_for_user

logger = logging.getLogger(__name__)

# Cap on PHI text stored in source_metadata (matches the ocr_text cap).
_OCR_TEXT_CAP = 5000

# Admin-managed feature-flag keys that override the static env OCR providers.
# The env settings (``COMPANION_OCR_PROVIDER`` / ``..._SHADOW_PROVIDER``) remain
# the fallback when no active config row is present.
_OCR_PRIMARY_FLAG = "ocr_primary_provider"
_OCR_SHADOW_FLAG = "ocr_shadow_provider"


async def _resolve_ocr_provider(
    db: AsyncSession, key: str, default: str
) -> str:
    """Resolve an OCR provider name from the admin feature flag, else ``default``.

    A flag value is ``{"provider": "<name>"}``. If no active config row exists
    the static env ``default`` is used. An explicitly-set empty provider is
    honoured (e.g. shadow disabled), so absence vs. empty are distinguished.
    """
    try:
        row = await config_service.get_by_key(
            db, ConfigCategory.FEATURE_FLAG, key
        )
    except Exception:
        # Never let a config-read failure break ingestion — fall back to env.
        logger.exception("OCR provider flag %s read failed; using env", key)
        return default
    if row is None:
        return default
    value = row.value
    if isinstance(value, dict):
        return str(value.get("provider") or "")
    return str(value or "")


def _min_confidence(values: list[float]) -> float | None:
    """Worst (minimum) reported per-page confidence in [0, 1], or None if none.

    The review-floor question is "should a human double-check this document?" and
    the extracted fields can come from ANY single page, so a strong page must
    never mask a weak one. Averaging did exactly that (a clean back page pulling
    a garbled statement page above the floor), so the cross-page aggregate that
    drives the floor is the *minimum*, not the mean — erring toward more review.
    """
    if not values:
        return None
    return max(0.0, min(1.0, min(values)))


async def _ocr_pages(
    provider_name: str, page_datas: list[bytes], mime_type: str
) -> tuple[str, int, float | None]:
    """Run ``provider_name`` over one or more page images.

    Returns ``(concatenated_text, total_ms, min_confidence)``. For multi-page
    the per-page texts are concatenated with the same ``--- Page N ---`` framing
    the primary uses. The confidence handed upward is the *worst* page's
    confidence (minimum over the pages that reported one), or ``None`` when the
    engine/service reported none — this value drives the OCR review floor, so a
    single bad page must not be averaged away by a good one.
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
        confs = [r.confidence for r in results if r.confidence is not None]
        return text, sum(r.ms for r in results), _min_confidence(confs)
    result = await provider.extract_text(page_datas[0], mime_type)
    return result.text, result.ms, result.confidence


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
    shadow_provider = await _resolve_ocr_provider(
        db, _OCR_SHADOW_FLAG, settings.ocr_shadow_provider
    )
    if not shadow_provider or shadow_provider == primary_provider:
        return
    try:
        shadow_text, shadow_ms, shadow_conf = await _ocr_pages(
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
            "shadow_confidence": shadow_conf,
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
    ocr_confidence: float | None = None

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
        primary_provider = await _resolve_ocr_provider(
            db, _OCR_PRIMARY_FLAG, settings.ocr_provider
        )

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
            raw_text, primary_ms, ocr_confidence = await _ocr_pages(
                primary_provider, page_datas, mime_type
            )
            # Log-and-observe: the OCR engine's own recognition confidence is
            # telemetry for tuning the review floor (see app.pipeline.confidence).
            # Score/char-count are not PHI; the text itself is never logged.
            logger.info(
                "OCR extracted %d characters from %d page(s) "
                "[provider=%s confidence=%s]",
                len(raw_text), len(page_datas), primary_provider,
                "n/a" if ocr_confidence is None else f"{ocr_confidence:.3f}",
            )

            # Store extracted text (keep original GCS path). PHI -> encrypt.
            if not doc.source_metadata:
                doc.source_metadata = {}
            doc.source_metadata["ocr_text"] = await encrypt_for_user(
                db, doc.user_id, raw_text[:_OCR_TEXT_CAP]
            )
            doc.source_metadata["ocr_complete"] = True
            doc.source_metadata["ocr_confidence"] = ocr_confidence
            doc.source_metadata["ocr_provider"] = primary_provider
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
        ocr_confidence=ocr_confidence,
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
