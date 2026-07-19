"""Unit tests for the fuzzy near-duplicate field-similarity logic.

The behavior that matters: a re-photographed document (same values, DIFFERENT
extraction formatting) is FLAGGED as a near-duplicate, but a DIFFERENT bill from
the same biller (same template, different amount/date) stays below the threshold
— so monthly bills are never false-positived.
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


# --- Value-aware similarity: the real re-photo the exact-string Jaccard missed ---

# The two Kirkwood scans a member actually took: same bill, different extraction
# formatting on sender (+/- "UTILITIES") and account mask ("*5369" vs "****5369"),
# IDENTICAL amount_due + due_date. Old exact-string Jaccard scored this 0.33.
_KIRKWOOD_A = {
    "sender": "CITY OF KIRKWOOD",
    "amount_due": -10.15,
    "due_date": "2026-07-22",
    "account_number_masked": "*5369",
}
_KIRKWOOD_B = {
    "sender": "CITY OF KIRKWOOD UTILITIES",
    "amount_due": -10.15,
    "due_date": "2026-07-22",
    "account_number_masked": "****5369",
}


def test_rephoto_with_formatting_variance_is_flagged():
    """(a) The genuine re-photo MUST now cross the threshold."""
    sim = dedup.field_similarity(_KIRKWOOD_A, _KIRKWOOD_B)
    assert sim >= dedup._SIM_THRESHOLD
    # Sanity: it clearly beats the old exact-string Jaccard (which was ~0.33).
    assert sim > 0.9


def test_same_biller_and_account_different_amount_and_date_not_flagged():
    """(b) Precision guard: same sender + account, DIFFERENT amount AND due date
    (next month's bill) MUST stay below the threshold."""
    next_month = {
        "sender": "CITY OF KIRKWOOD UTILITIES",
        "amount_due": 84.30,
        "due_date": "2026-08-22",
        "account_number_masked": "****5369",
    }
    assert dedup.field_similarity(_KIRKWOOD_A, next_month) < dedup._SIM_THRESHOLD


def test_masked_account_normalization():
    """(c) Account-mask normalization: mask chars/length don't matter, the
    trailing digits do."""
    assert dedup._value_similarity("account_number_masked", "*5369", "****5369") == 1.0
    assert dedup._value_similarity("account_number_masked", "5369", "****5369") == 1.0
    assert dedup._value_similarity("account_number_masked", "*1234", "****5369") == 0.0


def test_text_token_overlap():
    """(c) Free-text fields use token-set overlap, not all-or-nothing."""
    # Subset (one just adds a qualifier word) is a strong match.
    assert dedup._value_similarity(
        "sender", "city of kirkwood", "city of kirkwood utilities"
    ) >= 0.75
    # Wholly different senders share no tokens.
    assert dedup._value_similarity("sender", "city of kirkwood", "ameren missouri") == 0.0


def test_numbers_and_dates_are_exact_discriminators():
    """(c) Amount/date keep exact-value comparison — the key discriminators."""
    assert dedup._value_similarity("amount_due", "42.00", "42.0") == 1.0  # format-tolerant
    assert dedup._value_similarity("amount_due", "42.00", "51.75") == 0.0
    assert dedup._value_similarity("due_date", "2026-07-22", "2026-07-22") == 1.0
    assert dedup._value_similarity("due_date", "2026-07-22", "2026-08-22") == 0.0
