"""Medication reminder worker — sends push notifications at scheduled times.

Runs every minute via Cloud Scheduler. For each active medication,
checks if the current time matches a scheduled dose time (±5 min).
Creates a MedicationConfirmation record and sends a push notification.
Marks unconfirmed doses as missed after 2 hours.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.context import set_user_context
from app.db.session import async_session_factory, maintenance_session
from app.events.publisher import event_publisher
from app.events.schemas import MedicationMissedPayload
from app.models.medication import Medication, MedicationConfirmation
from app.services.push_notification_service import (
    notify_medication_reminder,
)

logger = logging.getLogger(__name__)


async def run_medication_reminder_for_user(user_id):
    """Send medication reminders for a specific user (ignoring time)."""
    async with async_session_factory() as db:
        try:
            # Tenant context for this user's per-user session (WS1 Phase 2).
            # No-op until RLS policies land. Transaction-local.
            await set_user_context(db, user_id)
            result = await db.execute(
                select(Medication)
                .where(
                    Medication.user_id == user_id,
                    Medication.is_active.is_(True),
                )
            )
            medications = result.scalars().all()

            now = datetime.utcnow()
            triggered = 0

            for med in medications:
                schedule_times = med.schedule
                if not isinstance(schedule_times, list):
                    continue

                # Use the first scheduled time for testing
                for sched_time_str in schedule_times:
                    try:
                        parts = sched_time_str.split(":")
                        sched_hour = int(parts[0])
                        sched_min = int(parts[1])
                    except (ValueError, IndexError):
                        continue

                    scheduled_dt = now.replace(
                        hour=sched_hour,
                        minute=sched_min,
                        second=0,
                        microsecond=0,
                    )

                    # Check if already exists
                    existing = await db.execute(
                        select(MedicationConfirmation).where(
                            MedicationConfirmation.medication_id
                            == med.id,
                            MedicationConfirmation.scheduled_at
                            == scheduled_dt,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    confirmation = MedicationConfirmation(
                        medication_id=med.id,
                        user_id=med.user_id,
                        scheduled_at=scheduled_dt,
                    )
                    db.add(confirmation)
                    await db.flush()

                    await notify_medication_reminder(
                        db, user_id, med.name
                    )
                    triggered += 1

            await db.commit()
            return {
                "total_medications": len(medications),
                "reminders_sent": triggered,
            }
        except Exception:
            await db.rollback()
            logger.exception(
                "Medication reminder failed for user %s",
                user_id,
            )
            raise


async def run_medication_reminder():
    """Check all medications and send reminders where due.

    An inherently CROSS-USER cron (sweeps every member's active medications and
    the stale-confirmation "mark missed" pass), so it runs under the maintenance
    (BYPASSRLS) session (WS1 Phase 2c). The per-med confirmation writes carry an
    explicit user_id (= the med's owner) and are fenced by the enforce_same_user
    trigger — child.user_id must equal the parent medication's user_id — so a bug
    can't write a confirmation for the wrong member even under bypass. Falls back
    to the normal session where the maintenance role is unconfigured (dev/test).
    (The per-user run_medication_reminder_for_user path stays companion_app + GUC.)
    """
    async with maintenance_session() as db:
        try:
            now = datetime.utcnow()

            # Find active medications with their users
            result = await db.execute(
                select(Medication)
                .where(Medication.is_active.is_(True))
                .options(selectinload(Medication.user))
            )
            medications = result.scalars().all()

            triggered = 0
            missed_marked = 0

            for med in medications:
                if not med.user or med.user.away_mode:
                    continue
                if med.user.account_status != "active":
                    continue

                schedule_times = med.schedule
                if not isinstance(schedule_times, list):
                    continue

                for sched_time_str in schedule_times:
                    try:
                        parts = sched_time_str.split(":")
                        sched_hour = int(parts[0])
                        sched_min = int(parts[1])
                    except (ValueError, IndexError):
                        continue

                    # Normalize to today's date at scheduled time
                    scheduled_dt = now.replace(
                        hour=sched_hour,
                        minute=sched_min,
                        second=0,
                        microsecond=0,
                    )

                    # Within ±5 minutes?
                    diff = abs(
                        (now - scheduled_dt).total_seconds()
                    )
                    if diff > 300:
                        continue

                    # Check if confirmation already exists
                    existing = await db.execute(
                        select(MedicationConfirmation).where(
                            MedicationConfirmation.medication_id
                            == med.id,
                            MedicationConfirmation.scheduled_at
                            == scheduled_dt,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    # Create pending confirmation
                    confirmation = MedicationConfirmation(
                        medication_id=med.id,
                        user_id=med.user_id,
                        scheduled_at=scheduled_dt,
                    )
                    db.add(confirmation)
                    await db.flush()

                    # Send push notification
                    await notify_medication_reminder(
                        db, med.user_id, med.name
                    )
                    triggered += 1
                    logger.info(
                        "Medication reminder sent: %s for %s",
                        med.name,
                        med.user.preferred_name,
                    )

            # Mark missed: unconfirmed confirmations older than 2h
            cutoff = now - timedelta(hours=2)
            stale = await db.execute(
                select(MedicationConfirmation)
                .where(
                    MedicationConfirmation.confirmed_at.is_(None),
                    MedicationConfirmation.missed.is_(False),
                    MedicationConfirmation.scheduled_at < cutoff,
                )
                .options(
                    selectinload(
                        MedicationConfirmation.medication
                    )
                )
            )
            for conf in stale.scalars().all():
                conf.missed = True
                missed_marked += 1
                if conf.medication:
                    await event_publisher.publish(
                        "medication.missed",
                        user_id=conf.medication.user_id,
                        payload=MedicationMissedPayload(
                            confirmation_id=conf.id,
                            medication_id=conf.medication_id,
                            scheduled_at=conf.scheduled_at,
                        ),
                    )

            await db.commit()
            logger.info(
                "Medication reminders: %d sent, %d marked missed",
                triggered,
                missed_marked,
            )
            return {
                "total_medications": len(medications),
                "reminders_sent": triggered,
                "marked_missed": missed_marked,
            }
        except Exception:
            await db.rollback()
            logger.exception("Medication reminder worker failed")
            raise
