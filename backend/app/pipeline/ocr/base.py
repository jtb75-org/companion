"""OCR provider ABC and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrResult:
    """The text an OCR engine extracted, plus provenance for A/B comparison."""

    text: str
    provider: str
    ms: int


class OcrProvider(ABC):
    """An OCR engine that turns image bytes into text."""

    #: Stable provider name (matches the factory key / config value).
    name: str

    @abstractmethod
    async def extract_text(self, image_bytes: bytes, mime_type: str) -> OcrResult:
        """Extract text from ``image_bytes`` (of ``mime_type``)."""
        raise NotImplementedError
