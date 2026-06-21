"""OCR provider abstraction.

Lets DocumentAI stay the primary engine while PaddleOCR (or any future engine)
can run in SHADOW for A/B comparison, gated by config flags. See
``app.config.settings.ocr_provider`` / ``ocr_shadow_provider`` / ``ocr_service_url``.
"""

from app.pipeline.ocr.base import OcrProvider, OcrResult
from app.pipeline.ocr.factory import available_providers, get_ocr_provider

__all__ = [
    "OcrProvider",
    "OcrResult",
    "available_providers",
    "get_ocr_provider",
]
