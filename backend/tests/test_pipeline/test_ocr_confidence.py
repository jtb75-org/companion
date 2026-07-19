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
from tests.conftest import requires_db

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


# ---------------------------------------------------------------------------
# Bug 1: the CROSS-PAGE review-floor aggregate is the WORST page, not the mean
#
# Real failure: a 2-page bill scored page0=0.74 (garbled statement page that
# misread the due date "2026"->"2020") and page1=0.97 (clean back page). The old
# mean = 0.86 > the 0.80 floor, so the floor did NOT fire and the member got a
# confident "pay by July 30, 2020" summary. The extracted fields can come from
# ANY page, so a good page must never mask a bad one -> aggregate on the min.
# ---------------------------------------------------------------------------


def _patch_provider_sequence(monkeypatch, name, pages):
    """Patch get_ocr_provider so successive extract_text calls return ``pages``.

    ``pages`` is a list of ``(text, confidence)`` handed out in page order.
    """
    calls = {"i": 0}

    class _Fake:
        def __init__(self, _name):
            self.name = _name

        async def extract_text(self, image_bytes, mime_type):
            text, conf = pages[calls["i"]]
            calls["i"] += 1
            return OcrResult(text=text, provider=name, ms=7, confidence=conf)

    monkeypatch.setattr(ingestion, "get_ocr_provider", lambda n: _Fake(n))


def test_min_confidence_picks_worst_page():
    # The exact real-failure case: worst page 0.74 must win over the clean 0.97.
    assert ingestion._min_confidence([0.74, 0.97]) == 0.74


def test_min_confidence_empty_is_none():
    assert ingestion._min_confidence([]) is None


def test_min_confidence_clamps_into_range():
    assert ingestion._min_confidence([1.5, 0.9]) == 0.9
    assert ingestion._min_confidence([-0.2, 0.9]) == 0.0


@pytest.mark.asyncio
async def test_ocr_pages_multipage_returns_worst_page_confidence(monkeypatch):
    """[0.74, 0.97] -> aggregate 0.74, which forces review (regression)."""
    _patch_provider_sequence(
        monkeypatch, "paddleocr",
        [("garbled due 2020", 0.74), ("clean back page", 0.97)],
    )
    text, _ms, conf = await ingestion._ocr_pages(
        "paddleocr", [b"p0", b"p1"], "image/jpeg"
    )
    assert conf == 0.74
    assert ocr_quality_forces_review(conf) is True
    # Both pages' text is still concatenated with the --- Page N --- framing.
    assert "--- Page 1 ---" in text and "--- Page 2 ---" in text


@pytest.mark.asyncio
async def test_ocr_pages_all_high_pages_not_flagged(monkeypatch):
    _patch_provider_sequence(
        monkeypatch, "paddleocr", [("clean", 0.95), ("clean", 0.97)],
    )
    _text, _ms, conf = await ingestion._ocr_pages(
        "paddleocr", [b"p0", b"p1"], "image/jpeg"
    )
    assert conf == 0.95
    assert ocr_quality_forces_review(conf) is False


@pytest.mark.asyncio
async def test_ocr_pages_single_page_confidence_unchanged(monkeypatch):
    """A single-page scan passes the page's own confidence straight through."""
    _patch_provider_sequence(monkeypatch, "paddleocr", [("only page", 0.62)])
    text, _ms, conf = await ingestion._ocr_pages(
        "paddleocr", [b"p0"], "image/jpeg"
    )
    assert conf == 0.62
    assert text == "only page"


@pytest.mark.asyncio
async def test_ocr_pages_all_none_confidence_is_none(monkeypatch):
    """No page reported a confidence -> None (floor stays inert)."""
    _patch_provider_sequence(
        monkeypatch, "paddleocr", [("a", None), ("b", None)],
    )
    _text, _ms, conf = await ingestion._ocr_pages(
        "paddleocr", [b"p0", b"p1"], "image/jpeg"
    )
    assert conf is None
    assert ocr_quality_forces_review(conf) is False


@pytest.mark.asyncio
async def test_multipage_scan_review_floor_uses_worst_page(monkeypatch):
    """End-to-end: the worst page's 0.74 reaches NormalizedDocument.ocr_confidence
    and would force review — the exact scan the mean let slip through."""
    doc = _FakeDoc(
        source_metadata={
            "content_type": "image/jpeg",
            "page_refs": ["r0", "r1"],
        }
    )
    db = _FakeDB(doc)

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "paddleocr")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "")
    _patch_provider_sequence(
        monkeypatch, "paddleocr",
        [("garbled due 2020", 0.74), ("clean back page", 0.97)],
    )

    result = await ingestion.process_camera_scan(db, doc.id)

    assert result.ocr_confidence == 0.74
    assert doc.source_metadata["ocr_confidence"] == 0.74
    assert ocr_quality_forces_review(result.ocr_confidence) is True


# ---------------------------------------------------------------------------
# Bug 2: source_metadata is a tracked mutable so in-place OCR writes persist
#
# A plain JSON/JSONB dict is NOT marked dirty by SQLAlchemy on in-place key
# assignment, so process_camera_scan's ``source_metadata["ocr_text"] = ...``
# writes were silently dropped by flush() whenever the dict was already
# non-empty (a real scan already carries page_refs/content_type from upload).
# MutableDict.as_mutable(JSONB) fixes every in-place mutation site at once.
# ---------------------------------------------------------------------------


def test_source_metadata_is_mutable_tracked():
    """Assigning a plain dict coerces to a MutableDict, so later in-place key
    assignment marks the column dirty and actually persists."""
    from sqlalchemy.ext.mutable import MutableDict

    from app.models.document import Document

    doc = Document(
        user_id=uuid.uuid4(),
        source_channel="camera_scan",
        raw_text_ref="scans/x/page_000.jpg",
    )
    doc.source_metadata = {"content_type": "image/jpeg", "page_refs": ["r0"]}
    assert isinstance(doc.source_metadata, MutableDict)
    # In-place mutation on the already-populated dict is what silently failed.
    doc.source_metadata["ocr_text"] = "enc:hello"
    assert doc.source_metadata["ocr_text"] == "enc:hello"


@requires_db
@pytest.mark.asyncio
async def test_process_camera_scan_persists_ocr_metadata(monkeypatch):
    """Round-trip regression: after process_camera_scan on a document whose
    source_metadata is ALREADY non-empty, a reload shows the OCR fields — the
    in-place-mutation drop that produced un-persisted ocr_text/confidence."""
    from app.db import session as db_module
    from app.models.document import Document
    from app.models.enums import SourceChannel
    from app.models.user import User

    async def _download(ref):
        return b"imagebytes"

    monkeypatch.setattr(ingestion.storage_service, "download", _download)
    monkeypatch.setattr(ingestion.settings, "ocr_provider", "paddleocr")
    monkeypatch.setattr(ingestion.settings, "ocr_shadow_provider", "")
    _patch_provider_sequence(monkeypatch, "paddleocr", [("SOME TEXT", 0.74)])

    async with db_module.async_session_factory() as s:
        user = User(
            email=f"ocr-persist-{uuid.uuid4()}@example.invalid",
            first_name="Ocr",
            last_name="Persist",
            preferred_name="Ocr",
            display_name="Ocr Persist",
            primary_language="en",
            voice_id="warm",
            pace_setting="normal",
            warmth_level="warm",
        )
        s.add(user)
        await s.flush()
        doc = Document(
            user_id=user.id,
            source_channel=SourceChannel.CAMERA_SCAN,
            raw_text_ref="scans/x/page_000.jpg",
            # Already non-empty at OCR time — the precondition for the bug.
            source_metadata={"content_type": "image/jpeg"},
        )
        s.add(doc)
        await s.commit()
        doc_id = doc.id
        user_id = user.id

    async with db_module.async_session_factory() as s:
        await ingestion.process_camera_scan(s, doc_id)
        await s.commit()

    try:
        async with db_module.async_session_factory() as s:
            reloaded = await s.get(Document, doc_id)
            meta = reloaded.source_metadata
            assert meta.get("ocr_provider") == "paddleocr"
            assert meta.get("ocr_confidence") == 0.74
            assert meta.get("ocr_complete") is True
            # ocr_text was written (dev marker "enc:" in test env) and survived.
            assert meta.get("ocr_text", "").startswith("enc:")
    finally:
        async with db_module.async_session_factory() as s:
            await s.execute(
                Document.__table__.delete().where(Document.id == doc_id)
            )
            await s.execute(User.__table__.delete().where(User.id == user_id))
            await s.commit()
