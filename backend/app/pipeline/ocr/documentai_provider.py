"""DocumentAI OCR provider — wraps Google Document AI (sync client)."""

from __future__ import annotations

import asyncio
import time

from app.config import settings
from app.pipeline.ocr.base import OcrProvider, OcrResult


def _ocr_with_document_ai(
    image_data: bytes, mime_type: str
) -> tuple[str, float | None]:
    """Run OCR using Google Document AI. Synchronous (sync google client).

    Returns ``(text, mean_confidence)``. ``mean_confidence`` is the mean of the
    per-token ``layout.confidence`` values Document AI already returns (or
    ``None`` if the response carries none). Best-effort telemetry — never raises
    on a missing/odd confidence shape.
    """
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
    return result.document.text, _mean_token_confidence(result.document)


def _mean_token_confidence(document) -> float | None:
    """Mean of per-token layout confidences across all pages, or ``None``."""
    try:
        scores: list[float] = [
            float(token.layout.confidence)
            for page in document.pages
            for token in page.tokens
            if token.layout.confidence
        ]
    except Exception:  # noqa: BLE001 — telemetry must never break OCR
        return None
    if not scores:
        return None
    return max(0.0, min(1.0, sum(scores) / len(scores)))


class DocumentAIProvider(OcrProvider):
    """Google Document AI. The pipeline's primary OCR engine."""

    name = "documentai"

    async def extract_text(self, image_bytes: bytes, mime_type: str) -> OcrResult:
        start = time.monotonic()
        text, confidence = await asyncio.to_thread(
            _ocr_with_document_ai, image_bytes, mime_type
        )
        ms = int((time.monotonic() - start) * 1000)
        return OcrResult(
            text=text, provider=self.name, ms=ms, confidence=confidence
        )
