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

import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import DocumentStatus

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


def _weight(key: str) -> float:
    """Stable identifying/discriminating fields weigh more than free text."""
    if any(
        tok in key
        for tok in ("amount", "balance", "total", "date", "due", "account", "mask")
    ):
        return _DISCRIMINATOR_WEIGHT
    return _TEXT_WEIGHT


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
    # Dates: stable discriminators — exact normalized comparison.
    if "date" in key or _DATE_RE.search(sa) or _DATE_RE.search(sb):
        return 1.0 if sa == sb else 0.0

    # Free text: token-set overlap.
    return _text_similarity(sa, sb)


def field_similarity(a: dict | None, b: dict | None) -> float:
    """Value-aware weighted similarity of two extracted-field dicts (0.0–1.0).

    Averages the per-field similarity across the SHARED keys, weighting the
    stable identifying fields (amount/date/account) above free text.
    """
    fa, fb = _clean_fields(a), _clean_fields(b)
    if not fa or not fb:
        return 0.0
    shared = set(fa) & set(fb)
    if not shared:
        return 0.0
    num = 0.0
    den = 0.0
    for key in shared:
        w = _weight(key)
        num += w * _value_similarity(key, fa[key], fb[key])
        den += w
    return num / den if den else 0.0


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
            best_sim, best_id = sim, cand.id

    return best_id if best_sim >= _SIM_THRESHOLD else None
