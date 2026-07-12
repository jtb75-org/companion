"""Admin API — Manual worker triggers."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.models.document import Document
from app.models.enums import DocumentStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/workers", tags=["Admin - Workers"])

_admin = require_admin_role("admin")


@router.post("/deletion")
async def trigger_deletion_worker(
    admin: AdminUser = Depends(_admin),
):
    """Manually trigger the deletion worker."""
    from app.workers.deletion_worker import run_deletion_worker

    result = await run_deletion_worker()
    return {"triggered": True, **result}


@router.post("/retention")
async def trigger_retention_worker(
    admin: AdminUser = Depends(_admin),
):
    """Manually trigger the retention worker."""
    from app.workers.retention import run_retention_worker

    result = await run_retention_worker()
    return {"triggered": True, **(result or {})}


@router.post("/escalation")
async def trigger_escalation_check(
    admin: AdminUser = Depends(_admin),
):
    """Manually trigger the escalation check."""
    from app.workers.escalation_check import run_escalation_check

    result = await run_escalation_check()
    return {"triggered": True, **result}


@router.post("/morning-checkin")
async def trigger_morning_checkin(
    user_id: uuid.UUID | None = Query(None),
    admin: AdminUser = Depends(_admin),
):
    """Manually trigger morning check-in. Optionally target a single user."""
    from app.workers.morning_trigger import (
        run_morning_trigger,
        run_morning_trigger_for_user,
    )

    if user_id:
        result = await run_morning_trigger_for_user(user_id)
    else:
        result = await run_morning_trigger(force=True)
    return {"triggered": True, **result}


@router.post("/medication-reminders")
async def trigger_medication_reminders(
    user_id: uuid.UUID | None = Query(None),
    admin: AdminUser = Depends(_admin),
):
    """Manually trigger medication reminders. Optionally target a single user."""
    from app.workers.medication_reminder import (
        run_medication_reminder,
        run_medication_reminder_for_user,
    )

    if user_id:
        result = await run_medication_reminder_for_user(user_id)
    else:
        result = await run_medication_reminder()
    return {"triggered": True, **result}


@router.post("/reprocess-documents")
async def reprocess_stuck_documents(
    admin: AdminUser = Depends(_admin),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Reprocess documents stuck in RECEIVED/PROCESSING."""
    from app.pipeline.orchestrator import process_document

    result = await db.execute(
        select(Document).where(
            Document.status.in_([
                DocumentStatus.RECEIVED,
                DocumentStatus.PROCESSING,
            ])
        )
    )
    docs = result.scalars().all()

    results = []
    for doc in docs:
        try:
            logger.info("Reprocessing doc %s", doc.id)
            pipeline_result = await process_document(
                db, doc.id, doc.user_id
            )
            await db.commit()
            results.append({
                "id": str(doc.id),
                "status": "processed",
                "classification": (
                    pipeline_result
                    .classification.classification
                ),
            })
        except Exception as e:
            await db.rollback()
            logger.exception(
                "Reprocess failed for %s", doc.id
            )
            results.append({
                "id": str(doc.id),
                "status": "failed",
                "error": str(e),
            })

    return {
        "reprocessed": len(results),
        "results": results,
    }
