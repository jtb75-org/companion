"""Morning check-in trigger — fires check-in for each user at their configured time.

In production, Cloud Scheduler calls this endpoint every minute.
The worker checks which users have a check-in due and fires events.
"""

import logging
from datetime import datetime, time

from sqlalchemy import select

from app.db.context import set_user_context
from app.db.session import async_session_factory, maintenance_session
from app.models.user import User
from app.notifications.briefing import generate_morning_briefing
from app.notifications.morning_checkin import assemble_morning_checkin
from app.services.push_notification_service import (
    notify_morning_briefing,
)

logger = logging.getLogger(__name__)


async def run_morning_trigger_for_user(user_id):
    """Trigger morning check-in for a specific user."""
    async with async_session_factory() as db:
        try:
            # Tenant context for this user's per-user session (WS1 Phase 2).
            # No-op until RLS policies land; then it scopes every query below
            # (incl. the users lookup) to this member. Transaction-local.
            await set_user_context(db, user_id)
            user = await db.get(User, user_id)
            if not user:
                return {"error": "User not found"}

            name = user.nickname or user.preferred_name
            checkin_data = await assemble_morning_checkin(
                db, user.id, name
            )
            briefing = await generate_morning_briefing(
                db, user.id, checkin_data
            )

            # Send push notification directly
            await notify_morning_briefing(
                db, user.id, briefing
            )
            await db.commit()
            logger.info(
                "Morning trigger for user %s: %s",
                name,
                briefing[:100],
            )
            return {
                "user": name,
                "briefing": briefing,
                "items": checkin_data.get("total_items", 0),
            }
        except Exception:
            await db.rollback()
            logger.exception(
                "Morning trigger failed for user %s",
                user_id,
            )
            raise


async def run_morning_trigger(force: bool = False):
    """Check all users and trigger morning check-ins where due.

    Discovery (which users are active, and their check-in time) is a cross-user
    read → runs under the maintenance session (bypass under RLS). Each due user's
    check-in runs RLS-scoped in run_morning_trigger_for_user (its own
    companion_app session with app.current_user_id set). Per-user failures are
    isolated so one bad user can't abort the whole cycle. (WS1 Phase 2c)
    """
    now = datetime.utcnow()
    current_hour = now.hour
    current_minute = now.minute

    # Cross-user discovery; detach the fields we filter on before the session closes.
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(User).where(User.account_status == "active")
        )
        candidates = [
            (u.id, u.checkin_time, u.away_mode) for u in result.scalars().all()
        ]

    triggered = 0
    for user_id, checkin_time, away_mode in candidates:
        ct = checkin_time or time(9, 0)
        if not force:
            if ct.hour != current_hour:
                continue
            if abs(ct.minute - current_minute) > 5:
                continue
        if away_mode:
            continue
        try:
            await run_morning_trigger_for_user(user_id)
            triggered += 1
        except Exception:
            logger.exception("Morning trigger failed for user %s", user_id)

    logger.info(
        f"Morning trigger: {triggered}/{len(candidates)} "
        f"check-ins fired at {current_hour}:{current_minute:02d}"
    )
    return {"total_users": len(candidates), "triggered": triggered}
