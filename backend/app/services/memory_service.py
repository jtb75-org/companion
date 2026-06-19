from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MemoryCategory, MemorySource
from app.models.functional_memory import FunctionalMemory
from app.services.field_crypto import encrypt_json_for_user


async def upsert_memory(
    db: AsyncSession,
    user_id: UUID,
    category: MemoryCategory,
    key: str,
    value,
    source: MemorySource,
) -> FunctionalMemory:
    """Create or update a functional memory.

    ``value`` is encrypted at rest (per-tenant envelope) before storage — this
    is the single sanctioned write path for FunctionalMemory.value, so all
    callers persist ciphertext, never plaintext.
    """
    blob = await encrypt_json_for_user(db, user_id, value)
    result = await db.execute(
        select(FunctionalMemory).where(
            FunctionalMemory.user_id == user_id,
            FunctionalMemory.category == category,
            FunctionalMemory.key == key,
        )
    )
    memory = result.scalar_one_or_none()
    if memory is None:
        memory = FunctionalMemory(
            user_id=user_id,
            category=category,
            key=key,
            value=blob,
            source=source,
        )
        db.add(memory)
    else:
        memory.value = blob
        memory.source = source
    await db.flush()
    return memory


async def list_memories(
    db: AsyncSession, user_id: UUID
) -> list[FunctionalMemory]:
    result = await db.execute(
        select(FunctionalMemory)
        .where(FunctionalMemory.user_id == user_id)
        .order_by(FunctionalMemory.category, FunctionalMemory.key)
    )
    return list(result.scalars().all())


async def delete_memory(
    db: AsyncSession, user_id: UUID, memory_id: UUID
) -> bool:
    result = await db.execute(
        select(FunctionalMemory).where(
            FunctionalMemory.id == memory_id,
            FunctionalMemory.user_id == user_id,
        )
    )
    memory = result.scalar_one_or_none()
    if memory is None:
        return False
    await db.delete(memory)
    await db.flush()
    return True


async def delete_all_memories(db: AsyncSession, user_id: UUID) -> int:
    # Get count first
    count_result = await db.execute(
        select(func.count())
        .select_from(FunctionalMemory)
        .where(FunctionalMemory.user_id == user_id)
    )
    count = count_result.scalar_one()

    await db.execute(
        delete(FunctionalMemory).where(FunctionalMemory.user_id == user_id)
    )
    await db.flush()
    return count
