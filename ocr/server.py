"""Self-hosted PaddleOCR HTTP service for Companion.

Replaces Google Document AI. Runs in SHADOW first for A/B comparison: the
backend's ``PaddleOCRProvider`` POSTs raw image bytes here and records the
result without ever affecting the primary pipeline.

Contract (must match backend/app/pipeline/ocr/paddleocr_provider.py):

    POST /ocr
      body:    raw image bytes
      header:  Content-Type: <mime>   (image/jpeg|png|heic, application/pdf)
      -> 200   {"text": "<all extracted text>", "ms": <int elapsed>}
      -> 500   {"error": "..."}

    GET /health -> 200 {"status": "ok"}   (no model load — readiness probe)

The PaddleOCR model is loaded lazily on the first /ocr request and cached for
the process lifetime. /health stays cheap so readiness never blocks on the
model. In the shipped image the models are already on disk (pre-downloaded at
build time), so the first-request cost is load-into-memory, not a network pull.
"""

from __future__ import annotations

import io
import logging
import time
from threading import Lock

import fitz  # PyMuPDF
import numpy as np
import pillow_heif
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("companion-ocr")

# Register HEIC/HEIF support with Pillow so Image.open() handles image/heic.
pillow_heif.register_heif_opener()

app = FastAPI(title="Companion OCR", version="1.0.0")

# Rasterize PDF pages at 200 DPI — a good accuracy/speed tradeoff for documents
# (receipts, letters, lab reports) without ballooning memory on dense pages.
_PDF_DPI = 200

_PDF_MIMES = {"application/pdf"}
_IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif"}

# Lazy singleton — PaddleOCR is heavy and (with use_gpu=False) thread-unsafe
# enough that we serialize calls behind a lock. One worker, one engine.
_ocr_engine = None
_ocr_lock = Lock()


def _get_engine():
    """Lazily build (or return) the cached PaddleOCR engine.

    CPU-only. Angle classification on (rotated phone photos of documents are
    common). lang='en' matches the Document AI processor's scope.
    """
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        log.info("Loading PaddleOCR engine (CPU)...")
        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=False, show_log=False)
        log.info("PaddleOCR engine ready.")
    return _ocr_engine


def _ocr_image(img: Image.Image) -> str:
    """Run PaddleOCR on a single PIL image and return its joined text lines."""
    engine = _get_engine()
    rgb = img.convert("RGB")
    arr = np.asarray(rgb)
    with _ocr_lock:
        result = engine.ocr(arr, cls=True)
    return _extract_lines(result)


def _extract_lines(result) -> str:
    """Flatten PaddleOCR's nested result into newline-joined text.

    PaddleOCR returns a per-image list; each entry is a list of
    ``[box, (text, confidence)]`` detections. Older/newer versions occasionally
    return ``None`` for a blank page, so guard every level.
    """
    if not result:
        return ""
    lines: list[str] = []
    for page in result:
        if not page:
            continue
        for det in page:
            try:
                text = det[1][0]
            except (IndexError, TypeError):
                continue
            if text:
                lines.append(text)
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> str:
    """Rasterize each PDF page at _PDF_DPI, OCR it, join pages with blank line."""
    pages: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=_PDF_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            pages.append(_ocr_image(img))
    return "\n\n".join(pages)


@app.get("/health")
async def health() -> JSONResponse:
    # Intentionally does NOT touch the model — used as the readiness probe.
    return JSONResponse({"status": "ok"})


@app.post("/ocr")
async def ocr(request: Request) -> JSONResponse:
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    body = await request.body()

    if not body:
        return JSONResponse({"error": "empty request body"}, status_code=500)

    start = time.monotonic()
    try:
        if mime in _PDF_MIMES:
            text = _extract_pdf(body)
        elif mime in _IMAGE_MIMES or mime.startswith("image/"):
            img = Image.open(io.BytesIO(body))
            text = _ocr_image(img)
        else:
            return JSONResponse(
                {"error": f"unsupported content-type: {mime!r}"}, status_code=500
            )
    except Exception as exc:  # noqa: BLE001 — surface any failure as 500 to the client
        log.exception("OCR failed for mime=%s", mime)
        return JSONResponse({"error": str(exc)}, status_code=500)

    ms = int((time.monotonic() - start) * 1000)
    return JSONResponse({"text": text, "ms": ms})
