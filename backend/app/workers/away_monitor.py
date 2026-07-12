"""Away mode monitor — checks for users in extended away mode.

Triggers Tier 1 caregiver alerts when a user has been in away mode
for 7+ days without checking in.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.context import set_user_context
from app.db.session import async_session_factory, maintenance_session
from app.events.publisher import event_publisher
from app.events.schemas import CaregiverAlertTriggeredPayload
from app.models.trusted_contact import TrustedContact
from app.models.user import User

logger = logging.getLogger(__name__)

AWAY_ALERT_THRESHOLD_DAYS = 7


async def run_away_monitor():
    """Check for users in extended away mode.

    Discovery (which users are in extended away) is a cross-user read → runs
    under the maintenance session (bypass under RLS). Each user's contacts are
    then read RLS-scoped as companion_app with app.current_user_id set (2c).
    """
    now = datetime.utcnow()
    threshold = now - timedelta(days=AWAY_ALERT_THRESHOLD_DAYS)

    # Cross-user discovery (detach the fields we need before the session closes).
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(User).where(
                User.away_mode.is_(True),
                User.away_expires_at.isnot(None),
                User.away_expires_at < threshold,
            )
        )
        away_users = [(u.id, u.away_expires_at) for u in result.scalars().all()]

    alerts_sent = 0
    for user_id, away_expires_at in away_users:
        async with async_session_factory() as db:
            await set_user_context(db, user_id)
            contacts_result = await db.execute(
                select(TrustedContact).where(
                    TrustedContact.user_id == user_id,
                    TrustedContact.is_active.is_(True),
                )
            )
            contacts = contacts_result.scalars().all()

            for contact in contacts:
                await event_publisher.publish(
                    "caregiver.alert.triggered",
                    user_id=user_id,
                    payload=CaregiverAlertTriggeredPayload(
                        trusted_contact_id=contact.id,
                        alert_type="extended_away_mode",
                        context={
                            "away_since": (
                                away_expires_at.isoformat()
                                if away_expires_at
                                else "unknown"
                            ),
                            "days_away": (
                                (now - away_expires_at).days
                                if away_expires_at
                                else 0
                            ),
                        },
                    ),
                )
                alerts_sent += 1

    logger.info(
        f"Away monitor: {len(away_users)} users in extended away, "
        f"{alerts_sent} alerts sent"
    )
    return {
        "users_in_extended_away": len(away_users),
        "alerts_sent": alerts_sent,
    }
