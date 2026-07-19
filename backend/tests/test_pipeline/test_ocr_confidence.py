"""Tests for OCR-confidence telemetry + the conservative review floor.

These lock in the log-and-observe recalibration work for PaddleOCR-primary:

* OCR engines' native recognition confidence is captured (was discarded).
* The confidence rides through ingestion onto the NormalizedDocument.
* Absence of a confidence (older service builds / email) is never fabricated.
* The OCR-quality review floor forces a review-inviting summary tone only when a
  *real* low confidence is present — and is inert when it is None.

Hermetic: no live OCR or cloud AI.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.pipeline import ingestion
from app.pipeline.confidence import (
    OCR_CONFIDENCE_REVIEW_FLOOR,
    ocr_quality_forces_review,
)
from app.pipeline.ocr import OcrResult
from app.pipeline.ocr.documentai_provider import _mean_token_confidence
from app.pipeline.ocr.paddleocr_provider import PaddleOCRProvider
from app.pipeline.schemas import ClassificationResult, ExtractionResult
from app.pipeline.summarization import summarize

# ---------------------------------------------------------------------------
# Provider captures the service-reported confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paddleocr_captures_confidence(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hi", "ms": 5, "confidence": 0.91})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    provider = PaddleOCRProvider(base_url="http://paddle.svc:8080")
    result = await provider.extract_text(b"x", "image/jpeg")
    assert result == OcrResult(text="hi", provider="paddleocr", ms=5, confidence=0.91)


@pytest.mark.asyncio
async def test_paddleocr_missing_confidence_is_none(monkeypatch):
    """Older service builds omit the key — confidence must be None, not 0.0."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"text": "hi", "ms": 5})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    provider = PaddleOCRProvider(base_url="http://paddle.svc:8080")
    result = await provider.extract_text(b"x", "image/jpeg")
    assert result.confidence is None


@pytest.mark.asyncio
async def test_paddleocr_bad_confidence_is_clamped(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"text": "hi", "ms": 5, "confidence": 2.5})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )
    provider = PaddleOCRProvider(base_url="http://paddle.svc:8080")
    result = await provider.extract_text(b"x", "image/jpeg")
    assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Ingestion threads confidence onto the NormalizedDocument + metadata
# ---------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.user_id = kw.get("user_id", uuid.uuid4())
        self.raw_text_ref = kw.get("raw_text_ref", "gs://bucket/scan.jpg")
        self.source_metadata = kw.get("source_metadata", {})
        self.source_channel = "camera_scan"


class _FakeDB:
    def __init__(self, doc):
        self._doc = doc

    async def get(self, _model, _id):
        return self._doc

    async def flush(self):
        return None


def _patch_provider(monkeypatch, name, text, confidence):
    class _Fake:
        def __init__(self, _name):
            self.name = _name

        async def extract_text(self, image_bytes, mime_type):
            return OcrResult(text=text, provider=name, ms=7, confidence=confidence)

    monkeypatch.setattr(ingestion, "get_ocr_provider", lambda n: _Fake(n))


@pytest.mark.asyncio
async def test_ingestion_records_ocr_confidence(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "paddleocr")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "")
    _patch_provider(monkeypatch, "paddleocr", "SOME TEXT", 0.62)

    result = await ingestion.process_camera_scan(db, doc.id)

    assert result.ocr_confidence == 0.62
    assert doc.source_metadata["ocr_confidence"] == 0.62
    assert doc.source_metadata["ocr_provider"] == "paddleocr"


@pytest.mark.asyncio
async def test_ingestion_confidence_none_when_unreported(monkeypatch):
    doc = _FakeDoc(source_metadata={"content_type": "image/jpeg"})
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "paddleocr")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "")
    _patch_provider(monkeypatch, "paddleocr", "SOME TEXT", None)

    result = await ingestion.process_camera_scan(db, doc.id)
    assert result.ocr_confidence is None
    assert doc.source_metadata["ocr_confidence"] is None


# ---------------------------------------------------------------------------
# The review floor
# ---------------------------------------------------------------------------


def test_floor_helper_none_never_forces_review():
    assert ocr_quality_forces_review(None) is False


def test_floor_helper_low_forces_review():
    assert ocr_quality_forces_review(OCR_CONFIDENCE_REVIEW_FLOOR - 0.01) is True


def test_floor_helper_high_does_not_force_review():
    assert ocr_quality_forces_review(0.99) is False


def _classification(confidence: float) -> ClassificationResult:
    return ClassificationResult(
        document_id=uuid.uuid4(),
        classification="medical",
        urgency_level="routine",
        confidence_score=confidence,
        classifier_tier=2,
    )


@pytest.mark.asyncio
async def test_low_ocr_forces_review_tone_despite_high_class_confidence():
    """High classification confidence + low OCR confidence -> review-inviting."""
    classification = _classification(0.98)  # would normally be stated plainly
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"provider": "Dr. Smith"},
    )
    result = await summarize(
        classification, extraction, db=None,
        ocr_confidence=0.40,  # well below the floor
    )
    assert "look at it together" in result.spoken_summary.lower()


@pytest.mark.asyncio
async def test_high_ocr_leaves_tone_unchanged():
    """A confident classification with no OCR quality problem is unhedged."""
    classification = _classification(0.98)
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"provider": "Dr. Smith"},
    )
    result = await summarize(
        classification, extraction, db=None, ocr_confidence=0.99,
    )
    assert "look at it together" not in result.spoken_summary.lower()


@pytest.mark.asyncio
async def test_missing_ocr_confidence_is_inert():
    """No OCR confidence (default) must not change behavior at all."""
    classification = _classification(0.98)
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"provider": "Dr. Smith"},
    )
    result = await summarize(classification, extraction, db=None)
    assert "look at it together" not in result.spoken_summary.lower()


# ---------------------------------------------------------------------------
# Blocker 1: the credit guard must honor the OCR review floor
# ---------------------------------------------------------------------------


def _bill_classification(confidence: float) -> ClassificationResult:
    return ClassificationResult(
        document_id=uuid.uuid4(),
        classification="bill",
        urgency_level="routine",
        confidence_score=confidence,
        classifier_tier=2,
    )


@pytest.mark.asyncio
async def test_low_ocr_forces_review_tone_on_zero_balance_bill():
    """class_conf 0.98 + ocr_conf 0.40 + amount_due 0 -> collaborative, NOT flat.

    Regression for the credit-guard bypass: a confident classifier on a garbled
    scan of a zero-balance bill must still invite review rather than assert
    "all set".
    """
    classification = _bill_classification(0.98)
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"sender": "City Utility", "amount_due": 0},
    )
    result = await summarize(
        classification, extraction, db=None, ocr_confidence=0.40,
    )
    assert "want to look at it together" in result.spoken_summary.lower()
    assert "all set" not in result.spoken_summary.lower()
    assert "let's check" in result.card_summary.lower()


@pytest.mark.asyncio
async def test_low_ocr_forces_review_tone_on_credit_bill():
    """Same for a genuine credit balance (amount_due < 0)."""
    classification = _bill_classification(0.98)
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"sender": "City Utility", "amount_due": -10.15},
    )
    result = await summarize(
        classification, extraction, db=None, ocr_confidence=0.40,
    )
    assert "want to look at it together" in result.spoken_summary.lower()
    assert "all set" not in result.spoken_summary.lower()


@pytest.mark.asyncio
async def test_high_ocr_zero_balance_bill_stays_direct():
    """Good scan + confident classifier keeps the reassuring flat wording."""
    classification = _bill_classification(0.98)
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"sender": "City Utility", "amount_due": 0},
    )
    result = await summarize(
        classification, extraction, db=None, ocr_confidence=0.99,
    )
    assert "all set" in result.spoken_summary.lower()
    assert "want to look at it together" not in result.spoken_summary.lower()


# ---------------------------------------------------------------------------
# Finding 2: Document AI mean confidence must count a genuine 0.0
# ---------------------------------------------------------------------------


def _doc(*confidences):
    """Fake Document AI document with one page of tokens carrying confidences.

    A ``None`` confidence models a genuinely-absent layout confidence.
    """
    tokens = [
        SimpleNamespace(layout=SimpleNamespace(confidence=c)) for c in confidences
    ]
    return SimpleNamespace(pages=[SimpleNamespace(tokens=tokens)])


def test_documentai_mean_counts_zero_confidence():
    # 0.0 must be included: mean(0.0, 1.0) = 0.5, not 1.0 (the pre-fix bug).
    assert _mean_token_confidence(_doc(0.0, 1.0)) == 0.5


def test_documentai_all_zero_is_zero_not_none():
    # An all-unreadable page reports 0.0, not None (floor must be able to fire).
    assert _mean_token_confidence(_doc(0.0, 0.0)) == 0.0


def test_documentai_skips_only_missing_confidences():
    # None is skipped; the real 0.0 and 0.5 are averaged -> 0.25.
    assert _mean_token_confidence(_doc(None, 0.0, 0.5)) == 0.25


def test_documentai_no_tokens_is_none():
    assert _mean_token_confidence(_doc()) is None
