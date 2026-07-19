"""Tests for bill credit handling — extraction sign parsing, prompt loading,
and the summarization credit-safety guard.

Context: a member uploaded a utility statement that was a CREDIT ("DO NOT PAY",
Total Amount Due -$10.15). The pipeline extracted +10.15 and D.D. told him to
"pay it by July 22" — telling a member to pay money they do not owe. These tests
lock in the fix on both the data side (signed amounts) and the member-facing
side (never instruct payment on a credit/zero balance).

All hermetic: no live cloud AI. The autouse stub_ai_backends fixture replaces the
LLM client; the summarization guard tests drive it deterministically.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.pipeline.extraction import (
    _DEFAULT_BILL_PROMPT,
    DEFAULT_PROMPTS,
    _get_extraction_prompt,
    _parse_amount,
    _validate_fields,
)
from app.pipeline.schemas import ClassificationResult, ExtractionResult
from app.pipeline.summarization import (
    _DEFAULT_SUMMARIZATION_PROMPT,
    _apply_credit_guard,
    _get_summarization_prompt,
    summarize,
)

# ---------------------------------------------------------------------------
# (a) Prompt loading never raises and returns the default with no config row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_prompt_defaults_without_db():
    """With no db (no config row available), the bill prompt is the default."""
    prompt = await _get_extraction_prompt(None, "bill")
    assert prompt == _DEFAULT_BILL_PROMPT
    assert prompt == DEFAULT_PROMPTS["bill"]


@pytest.mark.asyncio
async def test_extraction_prompt_unknown_type_falls_back_to_generic():
    prompt = await _get_extraction_prompt(None, "does_not_exist")
    assert prompt == DEFAULT_PROMPTS["generic"]


@pytest.mark.asyncio
async def test_summarization_prompt_defaults_without_db():
    prompt = await _get_summarization_prompt(None)
    assert prompt == _DEFAULT_SUMMARIZATION_PROMPT


# ---------------------------------------------------------------------------
# (b) Amount parsing preserves credits / negatives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("($10.15)", -10.15),
        ("-10.15", -10.15),
        ("-$10.15", -10.15),
        ("$10.15 CR", -10.15),
        ("10.15 CREDIT", -10.15),
        ("CREDIT", 0.0),
        ("$1,234.56", 1234.56),
        ("51.24", 51.24),
        (10.15, 10.15),
        (-10.15, -10.15),
    ],
)
def test_parse_amount_signs(raw, expected):
    assert _parse_amount(raw) == pytest.approx(expected)


def test_parse_amount_unparseable_is_none():
    assert _parse_amount("n/a") is None
    assert _parse_amount("") is None


def test_validate_fields_credit_stays_negative():
    fields = {
        "sender": "City of Kirkwood",
        "amount_due": "($10.15)",
        "due_date": "2026-07-22",
    }
    cleaned, missing = _validate_fields(fields, "bill")
    assert cleaned["amount_due"] == pytest.approx(-10.15)
    # A credit is a valid, present value — not "missing".
    assert "amount_due" not in missing


def test_validate_fields_plain_negative_and_credit_word():
    assert _validate_fields(
        {"sender": "X", "amount_due": "-10.15", "due_date": "2026-01-01"}, "bill"
    )[0]["amount_due"] == pytest.approx(-10.15)
    assert _validate_fields(
        {"sender": "X", "amount_due": "CREDIT", "due_date": "2026-01-01"}, "bill"
    )[0]["amount_due"] <= 0


# ---------------------------------------------------------------------------
# (c) Summarization credit guard — never instruct payment on a credit
# ---------------------------------------------------------------------------


def _classification(kind: str = "bill", confidence: float = 0.95):
    return ClassificationResult(
        document_id=uuid.uuid4(),
        classification=kind,
        urgency_level="routine",
        confidence_score=confidence,
    )


_FORBIDDEN = ("pay", "paid", "owe", "owed", "due")


def _has_payment_language(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in _FORBIDDEN)


def test_credit_guard_rewrites_payment_language():
    """Given a credit and payment-instruction text, the guard rewrites both."""
    spoken = "You have a bill from City of Kirkwood. You should pay it by July 22."
    card = "City of Kirkwood — $10.15 due July 22"
    fields = {"sender": "City of Kirkwood", "amount_due": -10.15}

    new_spoken, new_card = _apply_credit_guard(
        spoken, card, _classification("bill"), fields
    )

    assert not _has_payment_language(new_spoken)
    assert not _has_payment_language(new_card)
    assert "credit" in new_spoken.lower()
    assert "10.15" in new_spoken


def test_credit_guard_leaves_positive_amount_untouched():
    spoken = "You owe $45. You should pay it by Friday."
    card = "Electric Co — $45 due Friday"
    fields = {"sender": "Electric Co", "amount_due": 45.0}

    new_spoken, new_card = _apply_credit_guard(
        spoken, card, _classification("bill"), fields
    )
    assert (new_spoken, new_card) == (spoken, card)


def test_credit_guard_zero_balance():
    spoken = "You owe $0. Please pay by Friday."
    card = "Water Co — due Friday"
    fields = {"sender": "Water Co", "amount_due": 0.0}

    new_spoken, new_card = _apply_credit_guard(
        spoken, card, _classification("bill"), fields
    )
    assert not _has_payment_language(new_spoken)
    assert not _has_payment_language(new_card)


def test_credit_guard_ignores_non_bill():
    spoken = "You should pay attention to this letter."
    card = "Some Sender — action due"
    fields = {"sender": "Some Sender", "amount_due": -10.0}
    # A legal doc, not a bill — guard must not touch it.
    new_spoken, new_card = _apply_credit_guard(
        spoken, card, _classification("legal"), fields
    )
    assert (new_spoken, new_card) == (spoken, card)


@pytest.mark.asyncio
async def test_summarize_end_to_end_credit_safe(monkeypatch):
    """Full summarize(): an LLM that ignores the prompt and emits payment
    language is still corrected by the code guard for a credit bill."""
    import app.conversation.llm as llm_module
    from tests.stub_llm import StubGeminiClient

    payment_json = json.dumps(
        {
            "reasoning": "Utility bill with a due date.",
            "spoken": (
                "You have a bill from City of Kirkwood for $10.15. "
                "You should pay it by July 22."
            ),
            "card": "City of Kirkwood — $10.15 due July 22",
        }
    )
    stub = StubGeminiClient(reply=payment_json)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: stub)

    classification = _classification("bill")
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={"sender": "City of Kirkwood", "amount_due": -10.15},
    )

    result = await summarize(classification, extraction, db=None)

    assert not _has_payment_language(result.spoken_summary)
    assert not _has_payment_language(result.card_summary)
    assert "credit" in result.spoken_summary.lower()
    assert "10.15" in result.spoken_summary


@pytest.mark.asyncio
async def test_summarize_template_fallback_credit_safe(monkeypatch):
    """When the LLM path fails and the bill TEMPLATE (which says 'You owe...')
    runs, the guard still catches the credit."""
    # Default stub reply is non-JSON, so _llm_summarize returns None and the
    # template fallback runs — no monkeypatch of the client needed.
    classification = _classification("bill")
    extraction = ExtractionResult(
        document_id=classification.document_id,
        extracted_fields={
            "sender": "City of Kirkwood",
            "amount_due": -10.15,
            "due_date": "2026-07-22",
        },
    )

    result = await summarize(classification, extraction, db=None)

    assert not _has_payment_language(result.spoken_summary)
    assert not _has_payment_language(result.card_summary)
    assert "credit" in result.spoken_summary.lower()
