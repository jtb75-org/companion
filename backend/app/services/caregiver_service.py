from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.audit import CaregiverActivityLog
from app.models.bill import Bill
from app.models.enums import PaymentStatus
from app.models.medication import Medication, MedicationConfirmation
from app.models.todo import Todo
from app.models.trusted_contact import TrustedContact
from app.services.field_crypto import decrypt_row_field


async def list_contacts(
    db: AsyncSession, user_id: UUID
) -> list[TrustedContact]:
    result = await db.execute(
        select(TrustedContact)
        .where(TrustedContact.user_id == user_id)
        .order_by(TrustedContact.contact_name)
    )
    return list(result.scalars().all())


async def create_contact(
    db: AsyncSession, user_id: UUID, data: dict
) -> TrustedContact:
    contact = TrustedContact(user_id=user_id, **data)
    db.add(contact)
    await db.flush()
    return contact


async def update_contact(
    db: AsyncSession, user_id: UUID, contact_id: UUID, data: dict
) -> TrustedContact | None:
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.id == contact_id,
            TrustedContact.user_id == user_id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return None
    for key, value in data.items():
        setattr(contact, key, value)
    await db.flush()
    return contact


async def delete_contact(
    db: AsyncSession, user_id: UUID, contact_id: UUID
) -> bool:
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.id == contact_id,
            TrustedContact.user_id == user_id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return False
    await db.delete(contact)
    await db.flush()
    return True


async def pause_contact(
    db: AsyncSession, user_id: UUID, contact_id: UUID
) -> TrustedContact | None:
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.id == contact_id,
            TrustedContact.user_id == user_id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return None
    contact.is_active = False
    await db.flush()
    return contact


async def resume_contact(
    db: AsyncSession, user_id: UUID, contact_id: UUID
) -> TrustedContact | None:
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.id == contact_id,
            TrustedContact.user_id == user_id,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return None
    contact.is_active = True
    await db.flush()
    return contact


async def get_caregiver_activity(
    db: AsyncSession, user_id: UUID, limit: int = 50
) -> list[CaregiverActivityLog]:
    result = await db.execute(
        select(CaregiverActivityLog)
        .where(CaregiverActivityLog.user_id == user_id)
        .order_by(CaregiverActivityLog.occurred_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_alerts(db: AsyncSession, user_id: UUID) -> list[dict]:
    """Return active safety alerts: overdue meds, overdue bills, missed doses."""
    alerts: list[dict] = []
    today = date.today()
    now = datetime.utcnow()

    # Missed medication doses (confirmed_at is None and not marked missed, scheduled in the past)
    missed_result = await db.execute(
        select(MedicationConfirmation)
        .join(Medication)
        .where(
            Medication.user_id == user_id,
            Medication.is_active.is_(True),
            MedicationConfirmation.confirmed_at.is_(None),
            MedicationConfirmation.missed.is_(False),
            MedicationConfirmation.scheduled_at < now,
        )
        .order_by(MedicationConfirmation.scheduled_at.desc())
        .limit(10)
    )
    for conf in missed_result.scalars().all():
        alerts.append({
            "id": str(conf.id),
            "type": "medication",
            "message": "Missed medication dose",
            "timestamp": conf.scheduled_at.isoformat(),
        })

    # Overdue bills
    overdue_result = await db.execute(
        select(Bill).where(
            Bill.user_id == user_id,
            Bill.payment_status.in_(
                [PaymentStatus.PENDING, PaymentStatus.OVERDUE]
            ),
            Bill.due_date < today,
        )
    )
    for bill in overdue_result.scalars().all():
        alerts.append({
            "id": str(bill.id),
            "type": "bill",
            "message": (
                f"Overdue bill from {bill.sender}: "
                f"${bill.amount} was due "
                f"{bill.due_date.strftime('%B %d, %Y')}"
            ),
            "timestamp": bill.due_date.isoformat(),
        })

    return alerts


async def get_dashboard_summary(db: AsyncSession, user_id: UUID) -> dict:
    """Summarized status for caregiver dashboard."""
    today = date.today()
    now = datetime.utcnow()
    tomorrow = today + timedelta(days=1)

    # Active medications count
    med_count_result = await db.execute(
        select(func.count())
        .select_from(Medication)
        .where(Medication.user_id == user_id, Medication.is_active.is_(True))
    )
    active_medications = med_count_result.scalar_one()

    # Upcoming appointments (today or tomorrow)
    appt_result = await db.execute(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.user_id == user_id,
            Appointment.appointment_at >= now,
            Appointment.appointment_at < datetime.combine(
                tomorrow + timedelta(days=1), datetime.min.time()
            ),
        )
    )
    upcoming_appointments = appt_result.scalar_one()

    # Overdue bills
    overdue_result = await db.execute(
        select(func.count())
        .select_from(Bill)
        .where(
            Bill.user_id == user_id,
            Bill.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE]),
            Bill.due_date < today,
        )
    )
    overdue_bills = overdue_result.scalar_one()

    # Upcoming bills (pending, due in next 30 days)
    upcoming_bills_result = await db.execute(
        select(Bill).where(
            Bill.user_id == user_id,
            Bill.payment_status.in_(
                [PaymentStatus.PENDING, PaymentStatus.ACKNOWLEDGED]
            ),
            Bill.due_date >= today,
            Bill.due_date <= today + timedelta(days=30),
        ).order_by(Bill.due_date).limit(5)
    )
    upcoming_bills_rows = upcoming_bills_result.scalars().all()
    upcoming_bills = [
        {
            "description": b.sender,
            "due_date": b.due_date.isoformat(),
            "amount": f"${b.amount}",
        }
        for b in upcoming_bills_rows
    ]

    # Active todos (total and completed)
    todo_total_result = await db.execute(
        select(func.count())
        .select_from(Todo)
        .where(
            Todo.user_id == user_id,
            Todo.is_active.is_(True),
        )
    )
    todo_total = todo_total_result.scalar_one()

    todo_completed_result = await db.execute(
        select(func.count())
        .select_from(Todo)
        .where(
            Todo.user_id == user_id,
            Todo.is_active.is_(True),
            Todo.completed_at.is_not(None),
        )
    )
    todo_completed = todo_completed_result.scalar_one()
    active_todos = todo_total - todo_completed

    # Overdue bills list for display
    overdue_bills_list = []
    overdue_bills_result2 = await db.execute(
        select(Bill).where(
            Bill.user_id == user_id,
            Bill.payment_status.in_(
                [PaymentStatus.PENDING, PaymentStatus.OVERDUE]
            ),
            Bill.due_date < today,
        ).order_by(Bill.due_date).limit(5)
    )
    for b in overdue_bills_result2.scalars().all():
        overdue_bills_list.append({
            "description": b.sender,
            "due_date": b.due_date.isoformat(),
            "amount": f"${b.amount}",
        })

    # Active contacts
    contact_result = await db.execute(
        select(func.count())
        .select_from(TrustedContact)
        .where(
            TrustedContact.user_id == user_id,
            TrustedContact.is_active.is_(True),
        )
    )
    active_contacts = contact_result.scalar_one()

    alerts = await get_alerts(db, user_id)

    # Recent document reviews (last 10)
    from app.models.document import Document
    from app.models.pending_review import PendingReview
    review_result = await db.execute(
        select(PendingReview)
        .where(PendingReview.user_id == user_id)
        .order_by(PendingReview.created_at.desc())
        .limit(10)
    )
    reviews = review_result.scalars().all()
    recent_documents = []
    for r in reviews:
        doc = (
            await db.get(Document, r.document_id)
            if r.document_id else None
        )
        recent_documents.append({
            "review_id": str(r.id),
            "review_status": r.review_status,
            "recommended_action": r.recommended_action,
            "source_description": r.source_description,
            "card_summary": (
                await decrypt_row_field(db, doc, "card_summary")
                if doc else None
            ),
            "classification": (
                getattr(
                    doc.classification, "value",
                    str(doc.classification),
                )
                if doc and doc.classification else None
            ),
            "created_at": (
                r.created_at.isoformat()
                if r.created_at else None
            ),
            "resolved_at": (
                r.resolved_at.isoformat()
                if r.resolved_at else None
            ),
            "created_record_type": r.created_record_type,
        })

    # Compute status based on alerts
    status = "managing_well"
    if overdue_bills > 0 or len(alerts) > 0:
        status = "needs_attention"

    return {
        "status": status,
        "active_medications": active_medications,
        "upcoming_appointments": upcoming_appointments,
        "overdue_bills": overdue_bills,
        "overdue_bills_list": overdue_bills_list,
        "upcoming_bills": upcoming_bills,
        "tasks": {
            "total": todo_total,
            "completed": todo_completed,
        },
        "active_todos": active_todos,
        "active_contacts": active_contacts,
        "alert_count": len(alerts),
        "alerts": alerts,
        "recent_documents": recent_documents,
    }
