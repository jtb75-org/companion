"""Stage 5 — Routes documents and creates pending reviews or records."""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bill import Bill
from app.models.enums import (
    RecommendedAction,
    ReviewStatus,
)
from app.models.pending_review import PendingReview
from app.pipeline.schemas import (
    ClassificationResult,
    ExtractionResult,
    RoutingResult,
    SummarizationResult,
)
from app.services.record_creation_service import (
    create_appointment_from_fields,
    create_bill_from_fields,
)

logger = logging.getLogger(__name__)

# Routing table from the spec
ROUTING_TABLE: dict[str, str] = {
    "bill": "bills",
    "medical": "my_health",
    "legal": "home",
    "government": "home",
    "insurance": "my_health",
    "form": "home",
    "junk": "home",
    "personal": "home",
    "unknown": "home",
}

SOURCE_DESCRIPTIONS: dict[str, str] = {
    "camera_scan": "that picture you took",
    "email": "your email",
    "mail_station": "your mail",
}


async def route(
    db: AsyncSession,
    user_id: UUID,
    classification: ClassificationResult,
    extraction: ExtractionResult,
    summarization: SummarizationResult,
    care_model: str = "self_directed",
    source_channel: str = "camera_scan",
) -> RoutingResult:
    """Route document — create pending review or auto-create records."""

    # Clean up any existing pending reviews for this document
    # (idempotent — safe for reprocessing)
    await db.execute(
        delete(PendingReview).where(
            PendingReview.document_id
            == classification.document_id
        )
    )

    destination = ROUTING_TABLE.get(
        classification.classification, "home"
    )
    records_created: list[dict] = []
    suggested_action: str | None = None
    pending_review_id: UUID | None = None

    source_desc = SOURCE_DESCRIPTIONS.get(
        source_channel, "a document"
    )
    fields = dict(extraction.extracted_fields)
    # Include summaries in proposed_record_data so reviews
    # always have document context
    fields["_spoken_summary"] = summarization.spoken_summary
    fields["_card_summary"] = summarization.card_summary
    doc_type = classification.classification

    # PendingReview.proposed_record_data is encrypted at rest (per-tenant
    # envelope). Encrypt the dict once; every PendingReview constructed below
    # stores this ciphertext. Record-creation helpers still receive the plain
    # ``fields`` dict.
    from app.services.field_crypto import encrypt_json_for_user
    fields_blob = await encrypt_json_for_user(db, user_id, fields)

    if doc_type == "bill":
        suggested_action, pending_review_id = await _handle_bill(
            db, user_id, classification, fields, fields_blob,
            care_model, source_desc, records_created,
        )

    elif doc_type == "medical":
        suggested_action, pending_review_id = await _handle_medical(
            db, user_id, classification, fields, fields_blob,
            care_model, source_desc, records_created,
        )

    elif doc_type == "legal":
        # Legal always gets review (even managed)
        review = PendingReview(
            user_id=user_id,
            document_id=classification.document_id,
            review_status=ReviewStatus.PENDING,
            recommended_action=RecommendedAction.REVIEW_WITH_CONTACT,
            proposed_record_data=fields_blob,
            confidence_score=Decimal(
                str(classification.confidence_score)
            ),
            source_description=source_desc,
            is_urgent=True,
        )
        db.add(review)
        await db.flush()
        pending_review_id = review.id
        suggested_action = (
            "Review this with your trusted contact"
        )

    elif doc_type == "junk":
        suggested_action = None

    else:
        # Generic document — file only
        if care_model == "managed":
            review = PendingReview(
                user_id=user_id,
                document_id=classification.document_id,
                review_status=ReviewStatus.AUTO_CREATED,
                recommended_action=RecommendedAction.FILE_ONLY,
                proposed_record_data=fields_blob,
                confidence_score=Decimal(
                    str(classification.confidence_score)
                ),
                source_description=source_desc,
            )
            db.add(review)
            await db.flush()
        else:
            review = PendingReview(
                user_id=user_id,
                document_id=classification.document_id,
                review_status=ReviewStatus.PENDING,
                recommended_action=RecommendedAction.FILE_ONLY,
                proposed_record_data=fields_blob,
                confidence_score=Decimal(
                    str(classification.confidence_score)
                ),
                source_description=source_desc,
            )
            db.add(review)
            await db.flush()
            pending_review_id = review.id

    return RoutingResult(
        document_id=classification.document_id,
        routing_destination=destination,
        suggested_action=suggested_action,
        records_created=records_created,
        pending_review_id=pending_review_id,
    )


async def _handle_bill(
    db: AsyncSession,
    user_id: UUID,
    classification: ClassificationResult,
    fields: dict,
    fields_blob: str,
    care_model: str,
    source_desc: str,
    records_created: list[dict],
) -> tuple[str | None, UUID | None]:
    """Handle bill routing with care model awareness.

    ``fields`` is the plain dict (for record creation / duplicate checks);
    ``fields_blob`` is the encrypted ciphertext stored on PendingReview.
    """
    amount = fields.get("amount_due", "?")
    due_str = fields.get("due_date", "soon")

    # Check if past due
    is_past_due = _is_past_due(due_str)
    is_urgent = _is_due_soon(due_str, days=2)

    # Check for duplicate
    duplicate_bill = await _detect_duplicate_bill(
        db, user_id,
        fields.get("sender", ""),
        fields.get("amount_due"),
    )

    if care_model == "managed":
        # Auto-create record
        record = await create_bill_from_fields(
            db, user_id, fields,
            source_document_id=classification.document_id,
        )
        if record:
            records_created.append(
                {"type": "bill", "id": str(record.id)}
            )
        # Audit trail
        review = PendingReview(
            user_id=user_id,
            document_id=classification.document_id,
            review_status=ReviewStatus.AUTO_CREATED,
            recommended_action=RecommendedAction.ADD_BILL,
            proposed_record_data=fields_blob,
            confidence_score=Decimal(
                str(classification.confidence_score)
            ),
            source_description=source_desc,
            is_urgent=is_urgent,
            is_past_due=is_past_due,
            is_duplicate=duplicate_bill is not None,
            duplicate_of_id=(
                duplicate_bill.id if duplicate_bill else None
            ),
            created_record_type="bill",
            created_record_id=(
                record.id if record else None
            ),
            resolved_at=datetime.utcnow(),
        )
        db.add(review)
        await db.flush()
        action = f"Pay ${amount} — due {due_str}"
        return action, None

    # Self-directed — create pending review
    review = PendingReview(
        user_id=user_id,
        document_id=classification.document_id,
        review_status=ReviewStatus.PENDING,
        recommended_action=RecommendedAction.ADD_BILL,
        proposed_record_data=fields_blob,
        confidence_score=Decimal(
            str(classification.confidence_score)
        ),
        source_description=source_desc,
        is_urgent=is_urgent,
        is_past_due=is_past_due,
        is_duplicate=duplicate_bill is not None,
        duplicate_of_id=(
            duplicate_bill.id if duplicate_bill else None
        ),
    )
    db.add(review)
    await db.flush()

    action = f"Review bill from {fields.get('sender', '?')} — ${amount}"
    return action, review.id

async def _handle_medical(
    db: AsyncSession,
    user_id: UUID,
    classification: ClassificationResult,
    fields: dict,
    fields_blob: str,
    care_model: str,
    source_desc: str,
    records_created: list[dict],
) -> tuple[str | None, UUID | None]:
    """Handle medical document routing.

    ``fields`` is the plain dict; ``fields_blob`` is the encrypted ciphertext
    stored on PendingReview.
    """
    provider = fields.get("provider", "your doctor")
    notice = fields.get("nature_of_notice", "").lower()
    action = fields.get("required_action")
    date_time = fields.get("date_time")

    # If it's a retirement or practice change, don't treat it as an appointment
    if any(kw in notice for kw in ["retire", "change", "move", "leave"]):
        review = PendingReview(
            user_id=user_id,
            document_id=classification.document_id,
            review_status=ReviewStatus.PENDING,
            recommended_action=RecommendedAction.REVIEW_WITH_CONTACT,
            proposed_record_data=fields_blob,
            confidence_score=Decimal(str(classification.confidence_score)),
            source_description=source_desc,
        )
        db.add(review)
        await db.flush()
        return f"Important update from {provider}", review.id

    if care_model == "managed" and date_time:
        record = await create_appointment_from_fields(
            db, user_id, fields,
            source_document_id=classification.document_id,
        )
        if record:
            records_created.append(
                {"type": "appointment", "id": str(record.id)}
            )
        review = PendingReview(
            user_id=user_id,
            document_id=classification.document_id,
            review_status=ReviewStatus.AUTO_CREATED,
            recommended_action=RecommendedAction.ADD_APPOINTMENT,
            proposed_record_data=fields_blob,
            confidence_score=Decimal(
                str(classification.confidence_score)
            ),
            source_description=source_desc,
            created_record_type="appointment",
            created_record_id=(
                record.id if record else None
            ),
            resolved_at=datetime.utcnow(),
        )
        db.add(review)
        await db.flush()
        action = f"Check appointment with {provider}"
        return action, None

    # Self-directed — pending review
    review = PendingReview(
        user_id=user_id,
        document_id=classification.document_id,
        review_status=ReviewStatus.PENDING,
        recommended_action=RecommendedAction.ADD_APPOINTMENT,
        proposed_record_data=fields_blob,
        confidence_score=Decimal(
            str(classification.confidence_score)
        ),
        source_description=source_desc,
    )
    db.add(review)
    await db.flush()

    action = f"Review document from {provider}"
    return action, review.id


async def _detect_duplicate_bill(
    db: AsyncSession,
    user_id: UUID,
    sender: str,
    amount,
) -> Bill | None:
    """Check for similar bill in last 60 days."""
    if not sender or not amount:
        return None

    try:
        amount_val = Decimal(str(amount))
    except (ValueError, TypeError):
        return None

    cutoff = date.today() - timedelta(days=60)
    margin = amount_val * Decimal("0.1")

    result = await db.execute(
        select(Bill).where(
            Bill.user_id == user_id,
            Bill.sender.ilike(f"%{sender[:20]}%"),
            Bill.amount.between(
                amount_val - margin, amount_val + margin
            ),
            Bill.created_at >= cutoff,
        ).limit(1)
    )
    return result.scalar_one_or_none()


def _is_past_due(due_date_str: str | None) -> bool:
    """Check if a due date string is in the past."""
    if not due_date_str:
        return False
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y", "%m-%d-%Y",
        "%B %d, %Y", "%b %d, %Y",
    ):
        try:
            due = datetime.strptime(due_date_str, fmt).date()
            return due < date.today()
        except ValueError:
            continue
    return False


def _is_due_soon(
    due_date_str: str | None, days: int = 2
) -> bool:
    """Check if a due date is within N days."""
    if not due_date_str:
        return False
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y", "%m-%d-%Y",
        "%B %d, %Y", "%b %d, %Y",
    ):
        try:
            due = datetime.strptime(due_date_str, fmt).date()
            return due <= date.today() + timedelta(days=days)
        except ValueError:
            continue
    return False
