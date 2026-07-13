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

NON-DESTRUCTIVE by design: this only produces a hint (documents.possible_duplicate
_of) so the app can ASK the member. It never drops or merges a document — a
false positive must never lose a real document.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import DocumentStatus

# Jaccard over normalized field key=value pairs. High threshold: a re-photo of the
# same document yields near-identical extracted fields (~1.0); a different bill
# from the same biller differs on amount/date and lands well below this.
_SIM_THRESHOLD = 0.85
# Require a minimum amount of structured signal — sparse docs (e.g. junk mail with
# one field) would over-match. Below this we return no hint.
_MIN_FIELDS = 3
_MAX_CANDIDATES = 25


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _field_set(fields: dict | None) -> set[str]:
    """Normalized {key=value} pairs, skipping empty values."""
    if not fields:
        return set()
    out: set[str] = set()
    for key, value in fields.items():
        if value in (None, "", [], {}):
            continue
        out.add(f"{_norm(key)}={_norm(value)}")
    return out


def field_similarity(a: dict | None, b: dict | None) -> float:
    """Jaccard similarity of two extracted-field dicts (0.0–1.0)."""
    sa, sb = _field_set(a), _field_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


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
        cand_set = _field_set(cand_fields)
        if not cand_set:
            continue
        sim = len(this_set & cand_set) / len(this_set | cand_set)
        if sim > best_sim:
            best_sim, best_id = sim, cand.id

    return best_id if best_sim >= _SIM_THRESHOLD else None
