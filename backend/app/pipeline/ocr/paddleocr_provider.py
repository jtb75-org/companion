"""PaddleOCR HTTP provider — POSTs raw image bytes to the OCR service.

The PaddleOCR service is built/deployed separately. This client speaks to it
over HTTP:

    POST {ocr_service_url}/ocr
    Content-Type: <mime_type>      (the raw image bytes are the request body)

    -> 200 {"text": "...", "ms": N}

Any non-200 response or transport error raises, so the caller (shadow runner)
can record the failure without ever affecting the primary pipeline.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.pipeline.ocr.base import OcrProvider, OcrResult

# Generous but bounded — a shadow engine must never hang the pipeline. The
# shadow caller also wraps this in its own best-effort try/except.
_TIMEOUT_S = 30.0


class PaddleOCRProvider(OcrProvider):
    """Talks to the standalone PaddleOCR HTTP service."""

    name = "paddleocr"

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.ocr_service_url).rstrip("/")

    async def extract_text(self, image_bytes: bytes, mime_type: str) -> OcrResult:
        if not self._base_url:
            raise RuntimeError(
                "ocr_service_url is not configured; cannot reach PaddleOCR"
            )
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{self._base_url}/ocr",
                content=image_bytes,
                headers={"Content-Type": mime_type},
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"PaddleOCR service returned {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        ms = body.get("ms")
        if not isinstance(ms, int):
            ms = int((time.monotonic() - start) * 1000)
        return OcrResult(
            text=body.get("text", ""),
            provider=self.name,
            ms=ms,
            confidence=_coerce_confidence(body.get("confidence")),
        )


def _coerce_confidence(raw: object) -> float | None:
    """Clamp a service-reported confidence to [0, 1], or ``None`` if absent/bad.

    Older OCR service builds do not include a ``confidence`` key, so absence is
    normal and must never raise. Confidence is telemetry, not a gate.
    """
    if raw is None:
        return None
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None
