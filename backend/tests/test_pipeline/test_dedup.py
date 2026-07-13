"""Unit tests for the fuzzy near-duplicate field-similarity logic.

The behavior that matters: an identical document (same extracted fields) scores
~1.0, but a DIFFERENT bill from the same biller (same template, different amount/
date) scores below the threshold — so we don't false-positive monthly bills.
"""

from __future__ import annotations

from app.pipeline import dedup


def test_identical_fields_score_one():
    a = {"biller": "City of Kirkwood", "amount_due": "42.00", "due_date": "2026-07-01"}
    assert dedup.field_similarity(a, dict(a)) == 1.0


def test_same_biller_different_month_below_threshold():
    """Same biller/template, different amount + date = a DIFFERENT bill."""
    june = {"biller": "City of Kirkwood", "amount_due": "42.00", "due_date": "2026-06-01"}
    july = {"biller": "City of Kirkwood", "amount_due": "51.75", "due_date": "2026-07-01"}
    assert dedup.field_similarity(june, july) < dedup._SIM_THRESHOLD


def test_rephotograph_same_doc_above_threshold():
    """Same document re-captured: fields identical (OCR of the same values)."""
    a = {"biller": "Dr. Smith", "amount_due": "120.00", "due_date": "2026-08-15", "account": "X9"}
    b = {"biller": "Dr. Smith", "amount_due": "120.00", "due_date": "2026-08-15", "account": "X9"}
    assert dedup.field_similarity(a, b) >= dedup._SIM_THRESHOLD


def test_normalization_ignores_case_and_whitespace():
    a = {"Biller": "City  of Kirkwood", "Amount_Due": "42.00"}
    b = {"biller": "city of kirkwood", "amount_due": "42.00"}
    assert dedup.field_similarity(a, b) == 1.0


def test_empty_fields_score_zero():
    assert dedup.field_similarity({}, {"a": "1"}) == 0.0
    assert dedup.field_similarity(None, None) == 0.0
    # Empty-valued keys are skipped, so they don't inflate similarity.
    assert dedup._field_set({"a": "", "b": None, "c": []}) == set()


def test_field_set_skips_empty_values():
    s = dedup._field_set({"biller": "Acme", "note": "", "amount": "10"})
    assert s == {"biller=acme", "amount=10"}
