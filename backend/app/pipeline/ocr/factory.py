"""OCR provider factory."""

from __future__ import annotations

from app.pipeline.ocr.base import OcrProvider
from app.pipeline.ocr.documentai_provider import DocumentAIProvider
from app.pipeline.ocr.paddleocr_provider import PaddleOCRProvider

_PROVIDERS: dict[str, type[OcrProvider]] = {
    "documentai": DocumentAIProvider,
    "paddleocr": PaddleOCRProvider,
}


def available_providers() -> list[str]:
    """Names of the registered OCR providers (for validation / admin UI)."""
    return sorted(_PROVIDERS)


def get_ocr_provider(name: str) -> OcrProvider:
    """Construct the OCR provider registered under ``name``.

    Names: ``documentai`` (primary), ``paddleocr``.
    """
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown OCR provider {name!r}; known: {sorted(_PROVIDERS)}"
        )
    return cls()
