"""App API — Pending document reviews."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import User, require_complete_profile
from app.db import get_db
from app.models.document import Document
from app.models.enums import ReviewStatus
from app.models.pending_review import PendingReview
from app.services.field_crypto import decrypt_row_field

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("/pending")
async def get_pending_reviews(
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """List pending document reviews for the user."""
    result = await db.execute(
        select(PendingReview)
        .where(
            PendingReview.user_id == user.id,
            PendingReview.review_status.in_(
                [ReviewStatus.PENDING, ReviewStatus.PRESENTED]
            ),
        )
        .order_by(
            PendingReview.is_urgent.desc(),
            PendingReview.created_at.desc(),
        )
        .limit(5)
    )
    reviews = result.scalars().all()

    items = []
    for r in reviews:
        doc = (
            await db.get(Document, r.document_id)
            if r.document_id
            else None
        )
        # Fuzzy near-duplicate hint: if the pipeline flagged this doc as likely
        # the same as an earlier one, surface the earlier doc's id/date/type so
        # the app can ask the member ("looks like your bill from July 1 — same
        # one?"). Non-destructive; the member decides.
        possible_duplicate = None
        if doc and doc.possible_duplicate_of:
            earlier = await db.get(Document, doc.possible_duplicate_of)
            if earlier is not None:
                possible_duplicate = {
                    "document_id": str(earlier.id),
                    "received_at": (
                        earlier.received_at.isoformat()
                        if earlier.received_at
                        else None
                    ),
                    "classification": (
                        getattr(
                            earlier.classification, "value",
                            str(earlier.classification),
                        )
                        if earlier.classification
                        else None
                    ),
                }
        items.append({
            "id": str(r.id),
            "document_id": str(r.document_id) if r.document_id else None,
            "source_description": r.source_description,
            "recommended_action": r.recommended_action,
            "is_urgent": r.is_urgent,
            "is_past_due": r.is_past_due,
            "is_duplicate": r.is_duplicate,
            "possible_duplicate": possible_duplicate,
            "card_summary": (
                await decrypt_row_field(db, doc, "card_summary")
                if doc else None
            ),
            "classification": (
                getattr(
                    doc.classification, "value",
                    str(doc.classification),
                )
                if doc and doc.classification
                else None
            ),
            "proposed_data": await decrypt_row_field(
                db, r, "proposed_record_data"
            ),
            "created_at": (
                r.created_at.isoformat()
                if r.created_at
                else None
            ),
        })

    return {"reviews": items, "count": len(items)}
