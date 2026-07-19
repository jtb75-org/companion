"""Service layer for account deactivation and deletion."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.session import maintenance_session
from app.models.audit import DeletionAuditLog
from app.models.document import Document
from app.models.enums import AccountStatus, DeletionReason
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.services import storage_service

logger = logging.getLogger(__name__)


async def _get_grace_days() -> int:
    """Get deletion grace period from config, default 30 days."""
    try:
        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.system_config import SystemConfig

        async with async_session_factory() as db:
            result = await db.execute(
                select(SystemConfig).where(
                    SystemConfig.category == "deletion_settings",
                    SystemConfig.key == "grace_period_days",
                    SystemConfig.is_active.is_(True),
                )
            )
            config = result.scalar_one_or_none()
            if config and config.value:
                return int(config.value.get("days", 30))
    except Exception:
        pass
    return 30


# ---------------------------------------------------------------------------
# Object storage cleanup
# ---------------------------------------------------------------------------

def delete_storage_objects(uris: list[str]) -> tuple[int, int]:
    """Delete document objects from storage. Returns (deleted, failed)."""
    return storage_service.delete_objects(uris)


# ---------------------------------------------------------------------------
# Redis cleanup
# ---------------------------------------------------------------------------

async def clear_redis_keys(user_id: UUID) -> int:
    """Delete all Redis keys for a user. Returns count deleted."""
    try:
        from app.db.redis import get_redis
        r = get_redis()
        uid = str(user_id)
        patterns = [f"ctx:{uid}:*", f"session:{uid}:*", f"rate:*:{uid}", f"cache:section:{uid}:*"]
        count = 0
        for pattern in patterns:
            async for key in r.scan_iter(match=pattern):
                await r.delete(key)
                count += 1
        return count
    except Exception:
        logger.exception(f"Failed to clear Redis keys for user {user_id}")
        return 0


# ---------------------------------------------------------------------------
# Deactivation
# ---------------------------------------------------------------------------

async def deactivate_account(
    db: AsyncSession, user_id: UUID, initiated_by: str
) -> User:
    """Deactivate an account. Reversible."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if user.account_status == AccountStatus.DEACTIVATED:
        return user  # Idempotent

    now = datetime.utcnow()
    user.account_status = AccountStatus.DEACTIVATED
    user.deactivated_at = now
    user.away_mode = True

    # Deactivate all trusted contacts
    await db.execute(
        update(TrustedContact)
        .where(TrustedContact.user_id == user_id)
        .values(is_active=False)
    )

    await db.flush()
    logger.info(f"Account deactivated: user={user_id} by={initiated_by}")
    return user


async def reactivate_account(
    db: AsyncSession, user_id: UUID, initiated_by: str
) -> User:
    """Reactivate a deactivated account."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if user.account_status == AccountStatus.ACTIVE:
        return user  # Idempotent
    if user.account_status == AccountStatus.PENDING_DELETION:
        raise ValueError("Cancel deletion first before reactivating")

    user.account_status = AccountStatus.ACTIVE
    user.deactivated_at = None
    user.away_mode = False

    # Reactivate all trusted contacts
    await db.execute(
        update(TrustedContact)
        .where(TrustedContact.user_id == user_id)
        .values(is_active=True)
    )

    await db.flush()
    logger.info(f"Account reactivated: user={user_id} by={initiated_by}")
    return user


# ---------------------------------------------------------------------------
# Deletion request / cancellation
# ---------------------------------------------------------------------------

async def request_deletion(
    db: AsyncSession, user_id: UUID, reason: DeletionReason, initiated_by: str
) -> User | dict:
    """Request account deletion. If grace period is 0, deletes immediately."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if user.account_status == AccountStatus.PENDING_DELETION:
        return user  # Idempotent

    # Deactivate first if not already
    if user.account_status != AccountStatus.DEACTIVATED:
        await deactivate_account(db, user_id, initiated_by)

    grace_days = await _get_grace_days()

    if grace_days == 0:
        # Immediate deletion — no grace period
        logger.info(f"Immediate deletion (grace=0): user={user_id} by={initiated_by}")
        return await execute_deletion(db, user_id)

    now = datetime.utcnow()
    user.account_status = AccountStatus.PENDING_DELETION
    user.deletion_scheduled_at = now + timedelta(days=grace_days)

    await db.flush()
    logger.info(
        f"Deletion requested: user={user_id} by={initiated_by} "
        f"scheduled={user.deletion_scheduled_at.isoformat()}"
    )
    return user


async def cancel_deletion(
    db: AsyncSession, user_id: UUID, initiated_by: str
) -> User:
    """Cancel a pending deletion. Returns to deactivated state."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if user.account_status != AccountStatus.PENDING_DELETION:
        raise ValueError("Account is not pending deletion")

    user.account_status = AccountStatus.DEACTIVATED
    user.deletion_scheduled_at = None

    await db.flush()
    logger.info(f"Deletion cancelled: user={user_id} by={initiated_by}")
    return user


# ---------------------------------------------------------------------------
# Permanent deletion
# ---------------------------------------------------------------------------

async def execute_deletion(db: AsyncSession, user_id: UUID) -> dict:
    """Permanently delete a user and all associated data.

    Returns audit details dict.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    # 1. Collect GCS paths
    doc_result = await db.execute(
        select(Document.raw_text_ref).where(
            Document.user_id == user_id,
            Document.raw_text_ref.isnot(None),
            Document.raw_text_ref != "pending",
        )
    )
    gcs_paths = [row[0] for row in doc_result.all()]

    # 2. Collect caregiver emails for notification
    contact_result = await db.execute(
        select(TrustedContact.contact_email, TrustedContact.contact_name).where(
            TrustedContact.user_id == user_id,
            TrustedContact.contact_email.isnot(None),
        )
    )
    caregivers = [(row[0], row[1]) for row in contact_result.all()]

    # 3. Snapshot audit data
    entity_counts = {}
    for table_name, model in [
        ("documents", Document),
        ("trusted_contacts", TrustedContact),
    ]:
        count_result = await db.execute(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        entity_counts[table_name] = count_result.scalar_one()

    audit_details = {
        "email": user.email,
        "display_name": user.display_name,
        "entity_counts": entity_counts,
        "gcs_objects": len(gcs_paths),
        "caregivers_notified": len(caregivers),
    }

    # 4. Delete storage objects (best-effort)
    gcs_deleted, gcs_failed = delete_storage_objects(gcs_paths)
    audit_details["gcs_deleted"] = gcs_deleted
    audit_details["gcs_failed"] = gcs_failed

    # 5. Clear Redis keys
    redis_cleared = await clear_redis_keys(user_id)
    audit_details["redis_keys_cleared"] = redis_cleared

    # 6. Determine deletion reason
    reason = DeletionReason.USER_REQUEST
    if user.account_status != AccountStatus.PENDING_DELETION:
        reason = DeletionReason.ADMIN_REQUEST

    # 7. Create audit log entry (before deleting user, since user_id is not a FK)
    audit = DeletionAuditLog(
        user_id=user_id,
        entity_type="users",
        entity_id=user_id,
        reason=reason,
        details=audit_details,
    )
    db.add(audit)

    # 8. Remove caregiver relationships where this deleted user is the caregiver,
    #    matched by email across OTHER members. These are cross-member rows, so
    #    under trusted_contacts RLS they must be removed on the maintenance
    #    (BYPASSRLS) session — the member self-serve immediate-deletion path runs
    #    on a member-scoped session that would otherwise fail-close and orphan
    #    them. Bulk DELETEs; this commits independently of the outer deletion txn.
    from app.models.assignment_request import CaregiverAssignmentRequest

    async with maintenance_session() as mdb:
        tc_result = await mdb.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == user.email)
        )
        removed_caregiver_count = tc_result.rowcount or 0
        await mdb.execute(
            delete(CaregiverAssignmentRequest).where(
                CaregiverAssignmentRequest.caregiver_email == user.email
            )
        )
        await mdb.commit()

    audit_details["caregiver_roles_removed"] = removed_caregiver_count

    # 9. Delete admin record if exists
    from app.models.admin_user import AdminUser as AdminUserModel

    admin_result = await db.execute(
        select(AdminUserModel).where(AdminUserModel.email == user.email)
    )
    admin_record = admin_result.scalar_one_or_none()
    if admin_record:
        await db.delete(admin_record)
        audit_details["admin_record_deleted"] = True

    # 10. Identity-provider account cleanup: Firebase auth was retired, so there is no
    # Firebase user to delete here. Deleting the corresponding Authentik account is a
    # separate follow-up (needs the Authentik admin API + its own safety review); until
    # then a deleted member may leave an orphaned Authentik account (which can no longer
    # log in — the invite-only `users` row is gone, so /auth/login refuses it).

    # audit_details was mutated in-place AFTER db.add(audit) and after the admin lookup
    # above triggered an autoflush that serialized a PARTIAL dict. Plain-dict mutations to
    # a JSONB column aren't change-tracked (no MutableDict), so flag it dirty to persist
    # the complete details (incl. admin_record_deleted) on flush.
    flag_modified(audit, "details")

    # 11. Delete user row (CASCADE handles member's own data)
    await db.delete(user)
    await db.flush()

    logger.info(f"Account permanently deleted: user={user_id} details={audit_details}")
    return {"audit_details": audit_details, "caregivers": caregivers}
