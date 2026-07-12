"""Admin API — Escalation monitoring."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.notifications.escalation import get_open_escalations

_viewer = require_admin_role("viewer")

router = APIRouter(tags=["Admin - Escalations"])


@router.get("/admin/escalations")
async def list_escalations(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Open questions approaching or past thresholds."""
    items = await get_open_escalations(db)
    approaching = [i for i in items if i["pct_to_threshold"] >= 75]
    return {
        "escalations": items,
        "total": len(items),
        "approaching_threshold": len(approaching),
    }


@router.get("/admin/escalations/history")
async def escalation_history(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Escalation history."""
    # TODO: query historical escalations with date range
    return {"history": [], "total": 0}


@router.get("/admin/escalations/stats")
async def escalation_stats(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Escalation statistics."""
    items = await get_open_escalations(db)
    by_type = {}
    for item in items:
        ctx = item["context_type"]
        by_type[ctx] = by_type.get(ctx, 0) + 1
    return {
        "total_open": len(items),
        "by_context_type": by_type,
        "escalated_count": len(
            [i for i in items if i["status"] == "ESCALATED"]
        ),
    }
