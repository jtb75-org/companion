from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import event_publisher
from app.events.schemas import MedicationConfirmedPayload
from app.models.medication import Medication, MedicationConfirmation


async def list_medications(db: AsyncSession, user_id: UUID) -> list[Medication]:
    result = await db.execute(
        select(Medication)
        .where(Medication.user_id == user_id, Medication.is_active.is_(True))
        .order_by(Medication.name)
    )
    return list(result.scalars().all())


async def get_medication(
    db: AsyncSession, user_id: UUID, medication_id: UUID
) -> Medication | None:
    result = await db.execute(
        select(Medication).where(
            Medication.id == medication_id,
            Medication.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def create_medication(
    db: AsyncSession, user_id: UUID, data: dict
) -> Medication:
    medication = Medication(user_id=user_id, **data)
    db.add(medication)
    await db.flush()
    return medication


async def update_medication(
    db: AsyncSession, user_id: UUID, medication_id: UUID, data: dict
) -> Medication | None:
    medication = await get_medication(db, user_id, medication_id)
    if medication is None:
        return None
    for key, value in data.items():
        setattr(medication, key, value)
    await db.flush()
    return medication


async def delete_medication(
    db: AsyncSession, user_id: UUID, medication_id: UUID
) -> bool:
    medication = await get_medication(db, user_id, medication_id)
    if medication is None:
        return False
    medication.is_active = False
    await db.flush()
    return True


async def confirm_dose(
    db: AsyncSession, user_id: UUID, medication_id: UUID
) -> MedicationConfirmation:
    # Verify ownership
    medication = await get_medication(db, user_id, medication_id)
    if medication is None:
        raise ValueError("Medication not found")

    confirmation = MedicationConfirmation(
        medication_id=medication_id,
        user_id=user_id,
        scheduled_at=datetime.utcnow(),
        confirmed_at=datetime.utcnow(),
        missed=False,
    )
    db.add(confirmation)
    await db.flush()

    await event_publisher.publish(
        "medication.confirmed",
        user_id=user_id,
        payload=MedicationConfirmedPayload(
            confirmation_id=confirmation.id,
            medication_id=medication_id,
            scheduled_at=confirmation.scheduled_at,
            confirmed_at=confirmation.confirmed_at,
        ),
    )

    return confirmation


async def get_dose_history(
    db: AsyncSession, medication_id: UUID, limit: int = 30
) -> list[MedicationConfirmation]:
    result = await db.execute(
        select(MedicationConfirmation)
        .where(MedicationConfirmation.medication_id == medication_id)
        .order_by(MedicationConfirmation.scheduled_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
