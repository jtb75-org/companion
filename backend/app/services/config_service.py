from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.publisher import event_publisher
from app.events.schemas import ConfigUpdatedPayload
from app.models.system_config import ConfigAuditLog, SystemConfig


async def list_config(
    db: AsyncSession, category: str | None = None
) -> list[SystemConfig]:
    stmt = select(SystemConfig).where(SystemConfig.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(SystemConfig.category == category)
    stmt = stmt.order_by(SystemConfig.category, SystemConfig.key)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_config(
    db: AsyncSession, config_id: UUID
) -> SystemConfig | None:
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.id == config_id)
    )
    return result.scalar_one_or_none()


async def get_by_key(
    db: AsyncSession, category: str, key: str
) -> SystemConfig | None:
    """Fetch a single active config entry by (category, key), or None.

    Used by runtime consumers (e.g. the OCR pipeline) to read admin-managed
    feature flags without knowing the row's UUID.
    """
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == category,
            SystemConfig.key == key,
            SystemConfig.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def create_config(
    db: AsyncSession, data: dict
) -> SystemConfig:
    config = SystemConfig(**data)
    db.add(config)
    await db.flush()

    # Audit the creation too. For PHI-egress controls (e.g. the OCR engine flag)
    # the FIRST set is the most important event to capture — without this the
    # create path would leave the initial flip unrecorded (only updates were
    # audited before). old_value is null since the entry did not exist.
    audit_entry = ConfigAuditLog(
        config_id=config.id,
        category=config.category,
        key=config.key,
        old_value=None,
        new_value=config.value,
        changed_by=config.updated_by,
    )
    db.add(audit_entry)
    await db.flush()

    await event_publisher.publish(
        "config.updated",
        user_id=UUID(int=0),  # system event, no user context
        payload=ConfigUpdatedPayload(
            config_id=config.id,
            category=getattr(config.category, "value", str(config.category)),
            key=config.key,
            old_value=None,
            new_value={"value": config.value},
            changed_by=config.updated_by,
        ),
    )

    return config


async def update_config(
    db: AsyncSession,
    config_id: UUID,
    data: dict,
    changed_by: str,
    reason: str | None = None,
) -> SystemConfig | None:
    config = await get_config(db, config_id)
    if config is None:
        return None

    old_value = config.value

    # Increment version
    config.version += 1
    config.updated_by = changed_by

    # Apply new fields
    for key, value in data.items():
        setattr(config, key, value)

    # Create audit log entry
    audit_entry = ConfigAuditLog(
        config_id=config.id,
        category=config.category,
        key=config.key,
        old_value=old_value,
        new_value=config.value,
        changed_by=changed_by,
        reason=reason,
    )
    db.add(audit_entry)
    await db.flush()

    await event_publisher.publish(
        "config.updated",
        user_id=UUID(int=0),  # system event, no user context
        payload=ConfigUpdatedPayload(
            config_id=config.id,
            category=getattr(config.category, "value", str(config.category)),
            key=config.key,
            old_value={"value": old_value},
            new_value={"value": data["value"]},
            changed_by=changed_by,
        ),
    )

    return config


async def get_config_history(
    db: AsyncSession, config_id: UUID
) -> list[ConfigAuditLog]:
    result = await db.execute(
        select(ConfigAuditLog)
        .where(ConfigAuditLog.config_id == config_id)
        .order_by(ConfigAuditLog.changed_at.desc())
    )
    return list(result.scalars().all())


async def get_full_audit_log(
    db: AsyncSession, limit: int = 100
) -> list[ConfigAuditLog]:
    result = await db.execute(
        select(ConfigAuditLog)
        .order_by(ConfigAuditLog.changed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
