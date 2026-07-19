"""App API — Notification routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import User, require_complete_profile
from app.db import get_db
from app.notifications.morning_checkin import assemble_morning_checkin

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("")
async def list_notifications(
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """List notifications including morning check-in."""
    checkin = await assemble_morning_checkin(
        db, user.id, user.nickname or user.preferred_name
    )
    return {
        "checkin": checkin,
        "notifications": [],  # TODO: persistent notification store
    }


@router.patch("/{notification_id}")
async def acknowledge_notification(
    notification_id: str,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge/dismiss a notification."""
    # TODO: update notification status in DB.
    # SECURITY (pentest 2026-07-19): this is a no-op stub — it returns 200 for ANY id with
    # no existence/ownership check and ignores the body. Harmless now (notifications aren't
    # persisted, list returns empty), but when implemented it MUST look the notification up
    # scoped to `user.id` and 404 on miss/foreign-owner, or it's a straight IDOR.
    return {"id": notification_id, "acknowledged": True}


@router.get("/preferences")
async def get_preferences(
    user: User = Depends(require_complete_profile),
):
    """Get notification preferences."""
    return {
        "quiet_start": str(user.quiet_start) if user.quiet_start else None,
        "quiet_end": str(user.quiet_end) if user.quiet_end else None,
        "checkin_time": str(user.checkin_time) if user.checkin_time else None,
    }


@router.patch("/preferences")
async def update_preferences(
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Update notification preferences."""
    # TODO: accept preferences payload and update user
    return {"updated": True}
