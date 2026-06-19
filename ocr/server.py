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

The PaddleOCR model is built once and cached for the process lifetime. The
build is kicked off in a background thread at startup (warm-up) and is also
guarded so the first /ocr request can trigger it if warm-up hasn't finished.
The models are downloaded from PaddleOCR's CDN on first build (~16MB); they are
NOT baked into the image (instantiating PaddleOCR at build time segfaults in
kaniko). Egress to the model CDN is therefore required until the models are
pre-baked — track this alongside the egress NetworkPolicy.

CRITICAL: every blocking call (PaddleOCR build + inference + PDF rasterization)
is dispatched to a worker thread via ``asyncio.to_thread``, and warm-up runs in
its own thread, so the event loop — and therefore /health — never blocks. A
synchronous ~30-60s model load on the loop would starve the liveness probe and
get the pod SIGTERM'd mid-load (it never finishes); offloading prevents that.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
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
# enough that we serialize inference behind a lock. One worker, one engine.
# _build_lock guards construction (warm-up thread vs. first request racing);
# _ocr_lock guards inference.
_ocr_engine = None
_ocr_lock = Lock()
_build_lock = Lock()


def _get_engine():
    """Build (or return) the cached PaddleOCR engine. Blocking; thread-safe.

    CPU-only. Angle classification on (rotated phone photos of documents are
    common). lang='en' matches the Document AI processor's scope. Double-checked
    locking so the startup warm-up thread and a first request can't both build.
    """
    global _ocr_engine
    if _ocr_engine is None:
        with _build_lock:
            if _ocr_engine is None:
                from paddleocr import PaddleOCR

                log.info("Loading PaddleOCR engine (CPU)...")
                _ocr_engine = PaddleOCR(
                    use_angle_cls=True, lang="en", use_gpu=False, show_log=False
                )
                log.info("PaddleOCR engine ready.")
    return _ocr_engine


def _ocr_image_bytes(body: bytes) -> str:
    """Decode raw image bytes and OCR them. Blocking — call via to_thread."""
    img = Image.open(io.BytesIO(body))
    return _ocr_image(img)


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


@app.on_event("startup")
async def _warm_up() -> None:
    """Build the engine in a background thread so the first real request is warm.

    Runs off the event loop (its own thread) so the ~30-60s model load never
    blocks /health. If warm-up is still running when a request arrives,
    _get_engine's lock simply makes the request wait for the same build.
    """
    threading.Thread(target=_get_engine, name="ocr-warmup", daemon=True).start()


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
        # Offload ALL blocking work (model build + inference + PDF raster) to a
        # worker thread so the event loop — and /health — stay responsive.
        if mime in _PDF_MIMES:
            text = await asyncio.to_thread(_extract_pdf, body)
        elif mime in _IMAGE_MIMES or mime.startswith("image/"):
            text = await asyncio.to_thread(_ocr_image_bytes, body)
        else:
            return JSONResponse(
                {"error": f"unsupported content-type: {mime!r}"}, status_code=500
            )
    except Exception as exc:  # noqa: BLE001 — surface any failure as 500 to the client
        log.exception("OCR failed for mime=%s", mime)
        return JSONResponse({"error": str(exc)}, status_code=500)

    ms = int((time.monotonic() - start) * 1000)
    return JSONResponse({"text": text, "ms": ms})
