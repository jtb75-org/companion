"""OCR provider ABC and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrResult:
    """The text an OCR engine extracted, plus provenance for A/B comparison.

    ``confidence`` is the engine's own mean recognition confidence in [0, 1],
    or ``None`` when the engine/service did not report one. It is log-and-observe
    telemetry ONLY — it does not currently gate routing (see
    ``app.pipeline.confidence`` for why and for the tuning plan). Both PaddleOCR
    (per-line ``(text, score)``) and Document AI (per-token ``layout.confidence``)
    compute this natively; historically both providers discarded it.
    """

    text: str
    provider: str
    ms: int
    confidence: float | None = None


class OcrProvider(ABC):
    """An OCR engine that turns image bytes into text."""

    #: Stable provider name (matches the factory key / config value).
    name: str

    @abstractmethod
    async def extract_text(self, image_bytes: bytes, mime_type: str) -> OcrResult:
        """Extract text from ``image_bytes`` (of ``mime_type``)."""
        raise NotImplementedError
