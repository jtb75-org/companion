"""Deletion worker — executes pending account deletions past their grace period.

Runs nightly. Each user deletion commits independently so failures don't block others.
"""

import logging
from datetime import datetime

from sqlalchemy import select

from app.db.session import maintenance_session
from app.integrations.email_service import send_account_deleted_to_caregiver
from app.models.user import User
from app.services.account_lifecycle_service import execute_deletion

logger = logging.getLogger(__name__)


async def run_deletion_worker():
    """Execute pending account deletions past their grace period.

    Runs under the maintenance (BYPASSRLS) session (WS1 Phase 2c) — a privileged,
    inherently CROSS-USER operation: `execute_deletion` deletes the member's own
    rows AND intentionally cleans up references to them in OTHER members' data
    (their `trusted_contacts` / `caregiver_assignment_requests` keyed by the
    deleted member's email) plus `admin_users`. Those cross-user deletes cannot
    run under per-user RLS. Scoping is provided by the explicit
    `WHERE user_id == ...` / `email == ...` clauses in `execute_deletion`, not by
    RLS. (Mirrors HCC's dedicated shred principal.) A future hardening could drop
    to companion_app + GUC for the member's own-row deletes; deferred to avoid
    restructuring destructive code. Falls back to the normal session where the
    maintenance role is unconfigured (dev/test).
    """
    async with maintenance_session() as db:
        try:
            now = datetime.utcnow()
            result = await db.execute(
                select(User).where(
                    User.account_status == "pending_deletion",
                    User.deletion_scheduled_at <= now,
                )
            )
            users = result.scalars().all()

            deleted_count = 0
            for user in users:
                try:
                    name = user.preferred_name or user.display_name
                    deletion_result = await execute_deletion(db, user.id)
                    await db.commit()

                    # Notify caregivers after successful deletion
                    for email, cname in deletion_result.get("caregivers", []):
                        await send_account_deleted_to_caregiver(email, cname, name)

                    deleted_count += 1
                    logger.info(f"Deleted user {user.id} ({user.email})")
                except Exception:
                    await db.rollback()
                    logger.exception(f"Failed to delete user {user.id}")

            logger.info(
                f"Deletion worker complete: "
                f"{len(users)} pending, {deleted_count} deleted"
            )
            return {"pending": len(users), "deleted": deleted_count}
        except Exception:
            logger.exception("Deletion worker failed")
            raise
