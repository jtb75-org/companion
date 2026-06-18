"""Internal API — Worker endpoints for Cloud Scheduler.

Authenticated via X-Pipeline-Key header (same as Pub/Sub push).
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)


async def verify_pipeline_key(
    x_pipeline_key: str | None = Header(
        None, alias="X-Pipeline-Key"
    ),
):
    """Verify pipeline API key for service-to-service auth."""
    if not settings.pipeline_api_key:
        if settings.environment in ("development", "test"):
            return
        raise HTTPException(
            503, "Pipeline API key not configured"
        )
    if x_pipeline_key != settings.pipeline_api_key:
        raise HTTPException(401, "Invalid pipeline API key")


router = APIRouter(
    prefix="/api/internal/workers",
    tags=["Internal - Workers"],
    dependencies=[Depends(verify_pipeline_key)],
)


@router.post("/morning-checkin")
async def morning_checkin():
    """Called by Cloud Scheduler every minute."""
    from app.workers.morning_trigger import run_morning_trigger

    result = await run_morning_trigger()
    return result


@router.post("/medication-reminders")
async def medication_reminders():
    """Called by Cloud Scheduler every minute."""
    from app.workers.medication_reminder import (
        run_medication_reminder,
    )

    result = await run_medication_reminder()
    return result


@router.post("/escalation-check")
async def escalation_check():
    """Check open questions against escalation thresholds and alert caregivers.

    SAFETY-CRITICAL. Manages its own DB session; idempotent per run.
    Recommended cadence: every 15 minutes.
    """
    from app.workers.escalation_check import run_escalation_check

    result = await run_escalation_check()
    return result


@router.post("/away-monitor")
async def away_monitor():
    """Alert caregivers about users in extended away mode.

    Manages its own DB session. Recommended cadence: hourly.
    """
    from app.workers.away_monitor import run_away_monitor

    result = await run_away_monitor()
    return result


@router.post("/retention")
async def retention():
    """Enforce document retention policy and audit-log retention purge.

    Manages its own DB session. Recommended cadence: nightly.
    """
    from app.workers.retention import run_retention_worker

    result = await run_retention_worker()
    return result


@router.post("/ttl-purge")
async def ttl_purge():
    """Safety-net purge of expired Redis keys.

    Manages its own Redis connection. Recommended cadence: hourly.
    """
    from app.workers.ttl_purge import run_ttl_purge

    result = await run_ttl_purge()
    return result


@router.post("/account-deletion")
async def account_deletion():
    """Execute pending account deletions past their grace period.

    Manages its own DB session; each deletion commits independently.
    Recommended cadence: nightly.
    """
    from app.workers.deletion_worker import run_deletion_worker

    result = await run_deletion_worker()
    return result
