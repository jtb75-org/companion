from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.bill import Bill
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    PaymentStatus,
    UrgencyLevel,
)
from app.models.medication import Medication, MedicationConfirmation
from app.models.todo import Todo
from app.services.field_crypto import decrypt_row_field


async def _serialize_document(db: AsyncSession, doc: Document) -> dict:
    """Serialize a Document, decrypting the per-tenant encrypted fields.

    Returning a raw Document ORM row would leak the tagged ciphertext (``f2:``)
    for ``extracted_fields``/``spoken_summary``/``card_summary``. Mirrors
    documents.py::_serialize_document and get_today_section.
    """
    return {
        "id": str(doc.id),
        "source_channel": doc.source_channel,
        "classification": doc.classification,
        "confidence_score": doc.confidence_score,
        "urgency_level": doc.urgency_level,
        "extracted_fields": await decrypt_row_field(db, doc, "extracted_fields"),
        "spoken_summary": await decrypt_row_field(db, doc, "spoken_summary"),
        "card_summary": await decrypt_row_field(db, doc, "card_summary"),
        "routing_destination": doc.routing_destination,
        "page_count": doc.page_count,
        "status": doc.status,
        "received_at": doc.received_at,
        "processed_at": doc.processed_at,
        "acknowledged_at": doc.acknowledged_at,
    }


async def get_home_section(db: AsyncSession, user_id: UUID) -> dict:
    """Overview of all sections for the home screen."""
    today = date.today()
    now = datetime.utcnow()

    # Recent documents
    docs_result = await db.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.received_at.desc())
        .limit(5)
    )
    recent_documents = [
        await _serialize_document(db, d) for d in docs_result.scalars().all()
    ]

    # Active todos
    todos_result = await db.execute(
        select(Todo)
        .where(
            Todo.user_id == user_id,
            Todo.is_active.is_(True),
            Todo.completed_at.is_(None),
        )
        .order_by(Todo.due_date.asc().nulls_last())
        .limit(5)
    )
    active_todos = list(todos_result.scalars().all())

    # Upcoming appointments (next 7 days)
    appt_result = await db.execute(
        select(Appointment)
        .where(
            Appointment.user_id == user_id,
            Appointment.appointment_at >= now,
            Appointment.appointment_at < datetime.combine(
                today + timedelta(days=7), datetime.min.time()
            ),
        )
        .order_by(Appointment.appointment_at)
        .limit(3)
    )
    upcoming_appointments = list(appt_result.scalars().all())

    return {
        "recent_documents": recent_documents,
        "active_todos": active_todos,
        "upcoming_appointments": upcoming_appointments,
    }


async def get_health_section(db: AsyncSession, user_id: UUID) -> dict:
    """Health section: medications and upcoming medical appointments."""
    now = datetime.utcnow()

    # Active medications
    meds_result = await db.execute(
        select(Medication)
        .where(Medication.user_id == user_id, Medication.is_active.is_(True))
        .order_by(Medication.name)
    )
    medications = list(meds_result.scalars().all())

    # Upcoming appointments
    appt_result = await db.execute(
        select(Appointment)
        .where(
            Appointment.user_id == user_id,
            Appointment.appointment_at >= now,
        )
        .order_by(Appointment.appointment_at)
        .limit(10)
    )
    appointments = list(appt_result.scalars().all())

    return {
        "medications": medications,
        "appointments": appointments,
    }


async def get_bills_section(db: AsyncSession, user_id: UUID) -> dict:
    """Bills section: pending, overdue, and recently paid."""
    today = date.today()

    # Unpaid bills
    unpaid_result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            Bill.payment_status.in_([
                PaymentStatus.PENDING,
                PaymentStatus.ACKNOWLEDGED,
                PaymentStatus.OVERDUE,
            ]),
        )
        .order_by(Bill.due_date)
    )
    unpaid_bills = list(unpaid_result.scalars().all())

    # Recently paid (last 30 days)
    paid_result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            Bill.payment_status == PaymentStatus.PAID,
        )
        .order_by(Bill.updated_at.desc())
        .limit(10)
    )
    recently_paid = list(paid_result.scalars().all())

    overdue = [b for b in unpaid_bills if b.due_date < today]

    return {
        "unpaid_bills": unpaid_bills,
        "recently_paid": recently_paid,
        "overdue_count": len(overdue),
    }


async def get_plans_section(db: AsyncSession, user_id: UUID) -> dict:
    """Plans section: todos and upcoming appointments."""
    now = datetime.utcnow()

    # Active todos
    todos_result = await db.execute(
        select(Todo)
        .where(
            Todo.user_id == user_id,
            Todo.is_active.is_(True),
            Todo.completed_at.is_(None),
        )
        .order_by(Todo.due_date.asc().nulls_last())
    )
    todos = list(todos_result.scalars().all())

    # Upcoming appointments
    appt_result = await db.execute(
        select(Appointment)
        .where(
            Appointment.user_id == user_id,
            Appointment.appointment_at >= now,
        )
        .order_by(Appointment.appointment_at)
    )
    appointments = list(appt_result.scalars().all())

    return {
        "todos": todos,
        "appointments": appointments,
    }


async def get_today_section(db: AsyncSession, user_id: UUID) -> dict:
    """Priority view: most urgent items across all sections, sorted by urgency."""
    today = date.today()
    now = datetime.utcnow()
    tomorrow = today + timedelta(days=1)
    day_after_tomorrow = today + timedelta(days=2)
    in_48_hours = now + timedelta(hours=48)

    items: list[dict] = []

    # Medications due today (active, with no confirmed dose today)
    meds_result = await db.execute(
        select(Medication)
        .where(
            Medication.user_id == user_id,
            Medication.is_active.is_(True),
        )
    )
    medications = meds_result.scalars().all()

    start_of_today = datetime.combine(today, datetime.min.time())
    end_of_today = datetime.combine(tomorrow, datetime.min.time())

    for med in medications:
        # Check if there is already a confirmed dose today
        conf_result = await db.execute(
            select(MedicationConfirmation)
            .where(
                MedicationConfirmation.medication_id == med.id,
                MedicationConfirmation.confirmed_at.is_not(None),
                MedicationConfirmation.confirmed_at >= start_of_today,
                MedicationConfirmation.confirmed_at < end_of_today,
            )
            .limit(1)
        )
        if conf_result.scalar_one_or_none() is None:
            items.append({
                "type": "medication",
                "urgency": 1,
                "id": str(med.id),
                "title": f"Take {med.name} ({med.dosage})",
                "detail": med.frequency,
            })

    # Bills due within 48 hours
    bills_result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            Bill.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.ACKNOWLEDGED]),
            Bill.due_date <= in_48_hours.date(),
            Bill.due_date >= today,
        )
        .order_by(Bill.due_date)
    )
    for bill in bills_result.scalars().all():
        urgency = 0 if bill.due_date <= today else 2
        items.append({
            "type": "bill",
            "urgency": urgency,
            "id": str(bill.id),
            "title": f"Pay {bill.sender} - ${bill.amount}",
            "detail": f"Due {bill.due_date.isoformat()}",
        })

    # Overdue bills
    overdue_bills_result = await db.execute(
        select(Bill)
        .where(
            Bill.user_id == user_id,
            Bill.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.OVERDUE]),
            Bill.due_date < today,
        )
    )
    for bill in overdue_bills_result.scalars().all():
        items.append({
            "type": "bill",
            "urgency": 0,
            "id": str(bill.id),
            "title": f"OVERDUE: {bill.sender} - ${bill.amount}",
            "detail": f"Was due {bill.due_date.isoformat()}",
        })

    # Appointments today or tomorrow
    appt_result = await db.execute(
        select(Appointment)
        .where(
            Appointment.user_id == user_id,
            Appointment.appointment_at >= start_of_today,
            Appointment.appointment_at < datetime.combine(
                day_after_tomorrow, datetime.min.time()
            ),
        )
        .order_by(Appointment.appointment_at)
    )
    for appt in appt_result.scalars().all():
        is_today = appt.appointment_at.date() == today
        items.append({
            "type": "appointment",
            "urgency": 1 if is_today else 3,
            "id": str(appt.id),
            "title": f"{'Today' if is_today else 'Tomorrow'}: {appt.provider_name}",
            "detail": appt.appointment_at.strftime("%I:%M %p"),
        })

    # Overdue or urgent documents
    docs_result = await db.execute(
        select(Document)
        .where(
            Document.user_id == user_id,
            Document.urgency_level.in_([UrgencyLevel.URGENT, UrgencyLevel.ACT_TODAY]),
            Document.status.not_in([DocumentStatus.HANDLED, DocumentStatus.ACKNOWLEDGED]),
        )
    )
    for doc in docs_result.scalars().all():
        urgency = 0 if doc.urgency_level == UrgencyLevel.URGENT else 1
        detail = (
            await decrypt_row_field(db, doc, "card_summary")
            or await decrypt_row_field(db, doc, "spoken_summary")
            or ""
        )
        items.append({
            "type": "document",
            "urgency": urgency,
            "id": str(doc.id),
            "title": f"Document: {doc.classification or 'unclassified'}",
            "detail": detail,
        })

    # Sort by urgency (lower number = more urgent)
    items.sort(key=lambda x: x["urgency"])

    return {
        "items": items,
        "count": len(items),
    }
