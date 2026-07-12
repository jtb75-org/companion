"""Escalation check worker — monitors question tracker thresholds.

Runs every 15 minutes. Checks all open questions against their
escalation thresholds and triggers caregiver alerts when crossed.
"""

import logging

from sqlalchemy import select

from app.db.context import set_user_context
from app.db.session import async_session_factory, maintenance_session
from app.models.user import User
from app.notifications.escalation import check_escalations

logger = logging.getLogger(__name__)


async def run_escalation_check():
    """Check all users for questions past escalation thresholds.

    Each user is checked in its OWN session (tenant GUC set) and committed
    independently so one malformed user record cannot suppress every other
    user's caregiver escalation. This is a safety-critical path (abuse /
    medical emergency alerts), so isolation per user is required.

    Discovery (which users are active) is a cross-user read → runs under the
    maintenance session (bypass under RLS). Each user's check runs RLS-scoped
    as companion_app with app.current_user_id set (WS1 Phase 2c).
    """
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(User.id).where(User.account_status == "active")
        )
        user_ids = [row[0] for row in result.all()]

    total_escalated = 0
    failed = 0
    for user_id in user_ids:
        try:
            async with async_session_factory() as db:
                await set_user_context(db, user_id)
                escalated = await check_escalations(db, user_id)
                await db.commit()
                total_escalated += len(escalated)
        except Exception:
            failed += 1
            logger.exception(f"Escalation check failed for user {user_id}")

    # Per-user failures are isolated and reported via `failed`; a TOTAL failure
    # means a systemic problem and zero safety escalations went out this cycle,
    # so raise to surface the run as a failed Job instead of a silent clean exit.
    if user_ids and failed == len(user_ids):
        raise RuntimeError(f"Escalation check: all {failed} user(s) failed")

    logger.info(
        f"Escalation check complete: {len(user_ids)} users checked, "
        f"{total_escalated} escalated, {failed} failed"
    )
    return {
        "users_checked": len(user_ids),
        "total_escalated": total_escalated,
        "failed": failed,
    }
