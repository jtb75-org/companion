"""Fuzzy near-duplicate detection for documents (WS: document dedup, fuzzy tier).

Exact re-uploads (identical bytes) are caught at upload via content_fingerprint.
This catches a member RE-PHOTOGRAPHING the same document — different bytes, same
content — by comparing the pipeline's structured EXTRACTED FIELDS against the
member's earlier documents.

Why extracted fields (not raw OCR text): two different bills from the same biller
share most of their template TEXT (high text similarity → false positive), but
differ in the fields that matter — amount, due date, statement date. Comparing
the extracted key/value fields therefore distinguishes "same document again" from
"this month's bill" far better than text similarity.

Why VALUE-AWARE similarity (not Jaccard over exact key=value strings): the LLM
extraction has formatting variance. A member re-photographing the SAME City of
Kirkwood bill extracted:

    doc A: sender="CITY OF KIRKWOOD"            account_number_masked="*5369"
    doc B: sender="CITY OF KIRKWOOD UTILITIES"  account_number_masked="****5369"

with an IDENTICAL amount_due and due_date. Exact-string Jaccard scored this at
0.33 (only 2 of 6 pair-strings matched) and MISSED a genuine duplicate. So we
compare per field with type-aware rules:

  * masked-account fields → strip masking chars, compare trailing digits
    ("*5369" == "****5369" == "5369").
  * numbers / dates → exact value (these are the STABLE discriminators — a
    different bill from the same biller differs here, which must stay distinct).
  * free text (sender, provider, …) → token-set overlap, so "city of kirkwood"
    vs "city of kirkwood utilities" scores high instead of all-or-nothing.

The overall score is a weighted average of the per-field similarities across the
shared keys, weighting the stable identifying fields (amount_due, due_date,
account-last-4) above free text. This keeps PRECISION: a same-biller/same-account
bill for a DIFFERENT month (different amount + due date) still lands well below
the threshold, because those weight-2 discriminators score 0.

NON-DESTRUCTIVE by design: this only produces a hint (documents.possible_duplicate
_of) so the app can ASK the member. It never drops or merges a document — a
false positive must never lose a real document.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import DocumentStatus

logger = logging.getLogger(__name__)

# Weighted-average of per-field similarities (see module docstring), scale 0.0–1.0.
# A re-photo of the same document scores ~0.95+ (all discriminators match, only
# free text drifts); a different bill from the same biller differs on amount/date
# — weight-2 fields scoring 0 — and lands ~0.4, well below this.
_SIM_THRESHOLD = 0.80
# Require a minimum amount of structured signal on the INCOMING doc — sparse docs
# (e.g. junk mail with one field) would over-match. Below this we return no hint.
_MIN_FIELDS = 3
# And require at least this many keys shared with a candidate before scoring it,
# so a match can never be driven by a single coincidental field.
_MIN_SHARED = 2
_MAX_CANDIDATES = 25

# Free-text fields drift with extraction formatting; the identifying/amount/date
# fields are stable, so they carry more weight in the average.
_DISCRIMINATOR_WEIGHT = 2.0
_TEXT_WEIGHT = 1.0

# A masked-account value shows a run of masking chars followed by the last digits.
_MASK_CHARS = "*•·x"
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}")
# Date formats the extraction emits; canonicalized to ISO before comparison so
# "2026-07-22" and "07/22/2026" compare EQUAL (Python strptime is case-insensitive
# for %B/%b, so lowercased month names still parse).
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m/%d/%y",
    "%B %d, %Y",
    "%b %d, %Y",
)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _clean_fields(fields: dict | None) -> dict[str, object]:
    """Normalized-key -> value, dropping empty values (mirrors _field_set)."""
    if not fields:
        return {}
    out: dict[str, object] = {}
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        out[_norm(key)] = value
    return out


def _field_set(fields: dict | None) -> set[str]:
    """Normalized {key=value} pairs, skipping empty values.

    Retained for the incoming-doc signal guard (``len < _MIN_FIELDS``); the
    similarity itself is now value-aware (see ``field_similarity``).
    """
    if not fields:
        return set()
    out: set[str] = set()
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        out.add(f"{_norm(key)}={_norm(value)}")
    return out


def _is_masked_key(key: str) -> bool:
    return "mask" in key or "account" in key


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value))


def _parse_number(value: object) -> Decimal | None:
    """Parse a currency/number value, tolerant of $ , and whitespace."""
    s = re.sub(r"[,$\s]", "", str(value))
    if not s or not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _is_discriminator(key: str) -> bool:
    """A stable identifying field (amount / date / account) — the fields that
    actually distinguish "same doc" from "this month's bill"."""
    return any(
        tok in key
        for tok in ("amount", "balance", "total", "date", "due", "account", "mask")
    )


def _weight(key: str) -> float:
    """Stable identifying/discriminating fields weigh more than free text."""
    return _DISCRIMINATOR_WEIGHT if _is_discriminator(key) else _TEXT_WEIGHT


def _canonical_date(value: str) -> str | None:
    """Parse a date-like string to canonical ISO ``YYYY-MM-DD``, else None."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _text_similarity(a: str, b: str) -> float:
    """Token-set overlap of two free-text values (0.0–1.0).

    "city of kirkwood" vs "city of kirkwood utilities" → 0.75 (Jaccard), and a
    subset (one just adds/drops a qualifier word) is treated as a strong match.
    """
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 1.0 if a == b else 0.0
    if ta == tb:
        return 1.0
    if ta <= tb or tb <= ta:
        return 0.85
    return len(ta & tb) / len(ta | tb)


def _value_similarity(key: str, va: object, vb: object) -> float:
    """Per-field similarity, type-aware (0.0–1.0). See module docstring."""
    # Masked account numbers: compare trailing digits, tolerant of mask length.
    if _is_masked_key(key) or any(c in f"{va}{vb}" for c in _MASK_CHARS):
        da, db = _digits(va), _digits(vb)
        if not da or not db:
            return 1.0 if _norm(va) == _norm(vb) else 0.0
        n = min(len(da), len(db), 4)
        return 1.0 if da[-n:] == db[-n:] else 0.0

    # Numbers (amounts): exact value, tolerant of formatting.
    na, nb = _parse_number(va), _parse_number(vb)
    if na is not None and nb is not None:
        return 1.0 if na == nb else 0.0

    sa, sb = _norm(va), _norm(vb)
    # Dates: stable discriminators — exact by VALUE, but format-agnostic, so a
    # duplicate isn't missed just because one scan formatted the date differently.
    if "date" in key or _DATE_RE.search(sa) or _DATE_RE.search(sb):
        ca, cb = _canonical_date(sa), _canonical_date(sb)
        if ca is not None and cb is not None:
            return 1.0 if ca == cb else 0.0
        return 1.0 if sa == sb else 0.0

    # Free text: token-set overlap.
    return _text_similarity(sa, sb)


def field_similarity(a: dict | None, b: dict | None) -> float:
    """Value-aware weighted similarity of two extracted-field dicts (0.0–1.0).

    Weighted average of the per-field similarity, weighting the stable
    identifying fields (amount/date/account) above free text.

    A discriminator (amount/date/account) present on ONE doc but ABSENT on the
    other counts as a NON-MATCH (it stays in the denominator, contributes 0 to
    the numerator) — otherwise a candidate could score ~1.0 simply by omitting
    the field that would disagree (e.g. a sparse older doc with no amount can't
    "match" a bill whose amount differs). Free-text fields present on only one
    side are ignored (extraction drift shouldn't penalize), matching the intent
    that amount/date are the real discriminators.
    """
    fa, fb = _clean_fields(a), _clean_fields(b)
    if not fa or not fb:
        return 0.0
    keys_a, keys_b = set(fa), set(fb)
    shared = keys_a & keys_b
    num = 0.0
    den = 0.0
    for key in shared:
        w = _weight(key)
        num += w * _value_similarity(key, fa[key], fb[key])
        den += w
    # Missing discriminators = non-matches (denominator only).
    for key in keys_a ^ keys_b:
        if _is_discriminator(key):
            den += _DISCRIMINATOR_WEIGHT
    return num / den if den else 0.0


def _matching_shared_keys(a: dict | None, b: dict | None) -> list[str]:
    """Shared keys whose values MATCH (>= 0.99) — the keys that drove a match.

    Key NAMES only (no values), safe to log for threshold tuning.
    """
    fa, fb = _clean_fields(a), _clean_fields(b)
    out = []
    for key in set(fa) & set(fb):
        if _value_similarity(key, fa[key], fb[key]) >= 0.99:
            out.append(key)
    return sorted(out)


async def find_near_duplicate(
    db: AsyncSession,
    user_id: UUID,
    document_id: UUID,
    classification,
    extracted_fields: dict | None,
) -> UUID | None:
    """Return the id of an EARLIER same-classification document of this member
    whose extracted fields closely match ``extracted_fields``, else None.

    Runs on the member's pipeline session (tenant GUC set), so the candidate
    scan is RLS-scoped to this member — it can never match another member's doc.
    """
    this_set = _field_set(extracted_fields)
    if len(this_set) < _MIN_FIELDS:
        return None  # not enough structured signal to guess safely
    this_clean = _clean_fields(extracted_fields)

    candidates = (
        await db.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.id != document_id,
                Document.classification == classification,
                Document.status != DocumentStatus.FAILED,
                Document.extracted_fields.isnot(None),
            )
            .order_by(Document.received_at.desc())
            .limit(_MAX_CANDIDATES)
        )
    ).scalars().all()
    if not candidates:
        return None

    from app.services.field_crypto import decrypt_json_for_user

    best_id: UUID | None = None
    best_sim = 0.0
    best_fields: dict | None = None
    for cand in candidates:
        try:
            cand_fields = await decrypt_json_for_user(
                db, user_id, cand.extracted_fields
            )
        except Exception:
            continue
        cand_clean = _clean_fields(cand_fields)
        if not cand_clean:
            continue
        # Need enough overlapping keys that the score can't hinge on one field.
        if len(set(this_clean) & set(cand_clean)) < _MIN_SHARED:
            continue
        sim = field_similarity(extracted_fields, cand_fields)
        if sim > best_sim:
            best_sim, best_id, best_fields = sim, cand.id, cand_fields

    if best_id is not None and best_sim >= _SIM_THRESHOLD:
        # Traceability for tuning the 0.80 threshold against real data. Key NAMES
        # + score only — never raw PHI field values.
        logger.info(
            "near-duplicate flagged: document=%s matches=%s sim=%.3f "
            "matching_keys=%s threshold=%.2f",
            document_id, best_id, best_sim,
            _matching_shared_keys(extracted_fields, best_fields),
            _SIM_THRESHOLD,
        )
        return best_id
    return None
