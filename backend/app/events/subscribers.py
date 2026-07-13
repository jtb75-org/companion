from __future__ import annotations

import asyncio
import logging
import uuid

from app.events.publisher import on_event

logger = logging.getLogger(__name__)

# Tasks are fire-and-forget; keep strong refs so the event loop doesn't GC them
# mid-run (asyncio only holds weak references to running tasks).
_background_tasks: set[asyncio.Task] = set()


@on_event("document.received")
async def handle_document_received(envelope: dict):
    """Trigger the ingestion pipeline in-process (Pub/Sub was retired in the
    migration). Runs the pipeline as a detached background task so the upload
    request that published this event returns immediately — the doc processes
    asynchronously and the member is notified on completion."""
    payload = envelope.get("payload") or {}
    try:
        document_id = uuid.UUID(payload["document_id"])
        user_id = uuid.UUID(envelope["user_id"])
    except (KeyError, ValueError, TypeError):
        logger.error("document.received: unparseable envelope %s", envelope)
        return

    async def _run():
        from app.api.pipeline.document_handler import run_document_pipeline
        try:
            await run_document_pipeline(document_id, user_id)
        except Exception:
            logger.exception("Background pipeline failed for doc %s", document_id)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@on_event("document.processed")
async def handle_document_processed(envelope: dict):
    """When a document is processed, record pipeline metrics."""
    payload = envelope["payload"]
    logger.info(
        f"Document processed: {payload.get('document_id')} "
        f"classification={payload.get('classification')} "
        f"confidence={payload.get('confidence_score')}"
    )


@on_event("config.updated")
async def handle_config_updated(envelope: dict):
    """When config changes, invalidate cached values in Redis."""
    payload = envelope["payload"]
    category = payload.get("category", "")
    key = payload.get("key", "")

    logger.info(f"Config updated: {category}/{key} by {payload.get('changed_by')}")

    # Invalidate Redis cache for this config entry
    try:
        from app.db.redis import config_cache_key, get_redis
        r = get_redis()
        cache_key = config_cache_key(category, key)
        await r.delete(cache_key)
        await r.aclose()
        logger.info(f"Cache invalidated: {cache_key}")
    except Exception:
        logger.exception("Failed to invalidate config cache")


@on_event("question.threshold_crossed")
async def handle_question_threshold_crossed(envelope: dict):
    """When a question exceeds its escalation threshold, trigger caregiver alert."""
    payload = envelope["payload"]
    logger.info(
        f"Question threshold crossed: {payload.get('question_id')} "
        f"hours_open={payload.get('hours_open')} "
        f"threshold={payload.get('escalation_threshold_hours')}"
    )
    # In a full implementation, this would:
    # 1. Look up the user's trusted contacts at Tier 1+
    # 2. Compose a minimal-context alert
    # 3. Publish a caregiver.alert.triggered event
    # 4. Send push notification to caregiver


@on_event("medication.confirmed")
async def handle_medication_confirmed(envelope: dict):
    """Log medication confirmation for analytics."""
    payload = envelope["payload"]
    logger.info(
        f"Medication confirmed: {payload.get('medication_id')} "
        f"at {payload.get('confirmed_at')}"
    )


@on_event("medication.missed")
async def handle_medication_missed(envelope: dict):
    """When medication is missed, evaluate escalation."""
    payload = envelope["payload"]
    logger.info(
        f"Medication missed: {payload.get('medication_id')} "
        f"scheduled_at={payload.get('scheduled_at')}"
    )


@on_event("bill.overdue")
async def handle_bill_overdue(envelope: dict):
    """When a bill becomes overdue, notify user and evaluate caregiver alert."""
    payload = envelope["payload"]
    logger.info(
        f"Bill overdue: {payload.get('bill_id')} "
        f"sender={payload.get('sender')} "
        f"days_overdue={payload.get('days_overdue')}"
    )


@on_event("caregiver.dashboard.viewed")
async def handle_caregiver_dashboard_viewed(envelope: dict):
    """Log caregiver dashboard access for transparency."""
    payload = envelope["payload"]
    logger.info(
        f"Caregiver dashboard viewed: contact={payload.get('trusted_contact_id')} "
        f"user={payload.get('user_id')}"
    )


@on_event("checkin.morning.triggered")
async def handle_checkin_morning_triggered(envelope: dict):
    """Send push notification for morning check-in briefing."""
    from uuid import UUID

    from app.db.session import async_session_factory
    from app.services.push_notification_service import notify_morning_briefing

    payload = envelope["payload"]
    user_id = UUID(payload["user_id"])
    briefing = payload.get("briefing", "You have a few things to look at today.")

    async with async_session_factory() as db:
        await notify_morning_briefing(db, user_id, briefing)
        logger.info(f"Morning briefing sent to user {user_id}")
