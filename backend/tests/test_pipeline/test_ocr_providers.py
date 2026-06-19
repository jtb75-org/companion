"""Unit tests for the OCR provider abstraction, shadow A/B, and PHI encryption.

No live OCR services: DocumentAI and PaddleOCR are both mocked. The PaddleOCR
HTTP service is built separately, so its request shape is exercised against a
fake httpx transport rather than a real endpoint.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.pipeline import ingestion
from app.pipeline.ocr import OcrResult, get_ocr_provider
from app.pipeline.ocr.documentai_provider import DocumentAIProvider
from app.pipeline.ocr.paddleocr_provider import PaddleOCRProvider

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.user_id = kw.get("user_id", uuid.uuid4())
        self.raw_text_ref = kw.get("raw_text_ref", "gs://bucket/scan.jpg")
        self.source_metadata = kw.get("source_metadata", {})
        self.source_channel = "camera_scan"


class _FakeDB:
    """Minimal AsyncSession stand-in: get() returns a preset doc, flush no-ops."""

    def __init__(self, doc):
        self._doc = doc

    async def get(self, _model, _id):
        return self._doc

    async def flush(self):
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_returns_documentai():
    assert isinstance(get_ocr_provider("documentai"), DocumentAIProvider)


def test_factory_returns_paddleocr():
    assert isinstance(get_ocr_provider("paddleocr"), PaddleOCRProvider)


def test_factory_unknown_raises():
    with pytest.raises(ValueError):
        get_ocr_provider("nope")


def test_ocr_provider_default_is_documentai():
    from app.config import settings

    assert settings.ocr_provider == "documentai"


# ---------------------------------------------------------------------------
# PaddleOCR HTTP request shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paddleocr_request_shape(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hello world", "ms": 42})

    transport = httpx.MockTransport(handler)

    # Patch AsyncClient so the provider uses our mock transport.
    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)

    provider = PaddleOCRProvider(base_url="http://paddle.svc:8080")
    result = await provider.extract_text(b"\xff\xd8imgbytes", "image/jpeg")

    assert seen["method"] == "POST"
    assert seen["url"] == "http://paddle.svc:8080/ocr"
    assert seen["content_type"] == "image/jpeg"
    assert seen["body"] == b"\xff\xd8imgbytes"
    assert result == OcrResult(text="hello world", provider="paddleocr", ms=42)


@pytest.mark.asyncio
async def test_paddleocr_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(503, text="overloaded")
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    provider = PaddleOCRProvider(base_url="http://paddle.svc:8080")
    with pytest.raises(RuntimeError):
        await provider.extract_text(b"x", "image/png")


@pytest.mark.asyncio
async def test_paddleocr_unconfigured_url_raises():
    provider = PaddleOCRProvider(base_url="")
    with pytest.raises(RuntimeError):
        await provider.extract_text(b"x", "image/png")


# ---------------------------------------------------------------------------
# process_camera_scan: primary flow + shadow + encryption
# ---------------------------------------------------------------------------


def _patch_provider(monkeypatch, mapping):
    """Patch get_ocr_provider so each name returns a fake provider.

    ``mapping`` maps provider name -> callable(image_bytes, mime) -> str, or an
    Exception instance to raise.
    """

    class _Fake:
        def __init__(self, name):
            self.name = name

        async def extract_text(self, image_bytes, mime_type):
            spec = mapping[self.name]
            if isinstance(spec, Exception):
                raise spec
            return OcrResult(text=spec(image_bytes, mime_type), provider=self.name, ms=7)

    monkeypatch.setattr(
        ingestion, "get_ocr_provider", lambda name: _Fake(name)
    )


@pytest.mark.asyncio
async def test_primary_text_flows_to_raw_text(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "documentai")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "")
    _patch_provider(monkeypatch, {"documentai": lambda b, m: "PRIMARY TEXT"})

    result = await ingestion.process_camera_scan(db, doc.id)

    assert result.raw_text == "PRIMARY TEXT"
    # Stored ocr_text is encrypted (dev marker round-trips to the plaintext).
    from app.services.field_crypto import decrypt_value

    stored = doc.source_metadata["ocr_text"]
    assert stored != "PRIMARY TEXT"  # encrypted at rest
    assert await decrypt_value(db, doc.user_id, stored) == "PRIMARY TEXT"
    assert "ocr_shadow" not in doc.source_metadata


@pytest.mark.asyncio
async def test_shadow_records_comparison(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "documentai")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "paddleocr")
    _patch_provider(
        monkeypatch,
        {
            "documentai": lambda b, m: "the quick brown fox",
            "paddleocr": lambda b, m: "the quick brown fix",
        },
    )

    result = await ingestion.process_camera_scan(db, doc.id)

    # Primary still flows downstream unchanged.
    assert result.raw_text == "the quick brown fox"

    shadow = doc.source_metadata["ocr_shadow"]
    assert shadow["provider"] == "paddleocr"
    assert shadow["primary_provider"] == "documentai"
    assert shadow["primary_chars"] == len("the quick brown fox")
    assert shadow["shadow_chars"] == len("the quick brown fix")
    assert shadow["primary_ms"] == 7
    assert shadow["shadow_ms"] == 7
    assert 0.0 < shadow["similarity"] < 1.0

    # shadow_text is encrypted at rest.
    from app.services.field_crypto import decrypt_value

    assert shadow["shadow_text"] != "the quick brown fix"
    assert (
        await decrypt_value(db, doc.user_id, shadow["shadow_text"])
        == "the quick brown fix"
    )


@pytest.mark.asyncio
async def test_shadow_failure_never_breaks_pipeline(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "documentai")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "paddleocr")
    _patch_provider(
        monkeypatch,
        {
            "documentai": lambda b, m: "PRIMARY OK",
            "paddleocr": RuntimeError("paddle service down"),
        },
    )

    result = await ingestion.process_camera_scan(db, doc.id)

    # Primary succeeds despite the shadow blowing up.
    assert result.raw_text == "PRIMARY OK"
    assert "ocr_shadow" not in doc.source_metadata
    from app.services.field_crypto import decrypt_value

    assert (
        await decrypt_value(db, doc.user_id, doc.source_metadata["ocr_text"])
        == "PRIMARY OK"
    )


@pytest.mark.asyncio
async def test_shadow_skipped_when_same_as_primary(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "documentai")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "documentai")
    _patch_provider(monkeypatch, {"documentai": lambda b, m: "X"})

    await ingestion.process_camera_scan(db, doc.id)
    assert "ocr_shadow" not in doc.source_metadata


@pytest.mark.asyncio
async def test_multipage_primary_and_shadow_concatenate(monkeypatch):
    doc = _FakeDoc(
        source_metadata={
            "content_type": "image/jpeg",
            "page_refs": ["gs://b/p1.jpg", "gs://b/p2.jpg"],
        }
    )
    db = _FakeDB(doc)

    async def _download(ref):
        return ref.encode()

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "documentai")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "paddleocr")
    _patch_provider(
        monkeypatch,
        {
            "documentai": lambda b, m: f"P[{b.decode()}]",
            "paddleocr": lambda b, m: f"P[{b.decode()}]",
        },
    )

    result = await ingestion.process_camera_scan(db, doc.id)

    assert "--- Page 1 ---" in result.raw_text
    assert "--- Page 2 ---" in result.raw_text
    # Identical engines -> perfect similarity over concatenated text.
    assert doc.source_metadata["ocr_shadow"]["similarity"] == 1.0


# ---------------------------------------------------------------------------
# Encryption round-trip seen by a downstream reader (embeddings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeddings_reader_decrypts_ocr_text(monkeypatch):
    """The embeddings stage must see the decrypted OCR text."""
    from app.services.field_crypto import encrypt_for_user

    user_id = uuid.uuid4()
    doc = _FakeDoc(user_id=user_id, source_metadata={})
    db = _FakeDB(doc)
    doc.source_metadata["ocr_text"] = await encrypt_for_user(
        db, user_id, "decrypted ocr body"
    )

    from app.services.field_crypto import decrypt_value

    seen = await decrypt_value(db, user_id, doc.source_metadata["ocr_text"])
    assert seen == "decrypted ocr body"
