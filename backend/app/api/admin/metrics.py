"""Admin API — Business metrics."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.models.document import Document
from app.models.user import User

router = APIRouter(prefix="/admin/metrics", tags=["Admin - Metrics"])

_viewer = require_admin_role("viewer")


@router.get("/engagement")
async def engagement_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """User engagement metrics."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # Active users = users with account_status='active'
    total_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.account_status == "active"
        )
    )
    active_users = total_result.scalar_one()

    # Users created in last 7 days
    new_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.created_at >= week_ago,
            User.account_status == "active",
        )
    )
    new_users_7d = new_result.scalar_one()

    return {
        "active_users": active_users,
        "new_users_7d": new_users_7d,
    }


@router.get("/onboarding")
async def onboarding_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Onboarding funnel metrics."""
    # Total users
    total_result = await db.execute(
        select(func.count()).select_from(User)
    )
    total = total_result.scalar_one()

    # Users with completed profile (first_name + last_name set)
    completed_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.first_name.isnot(None),
            User.last_name.isnot(None),
        )
    )
    completed = completed_result.scalar_one()

    # Invited but not yet active
    invited_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.account_status == "invited"
        )
    )
    pending_invites = invited_result.scalar_one()

    return {
        "total_accounts": total,
        "profiles_completed": completed,
        "completion_rate": round(completed / total, 2) if total > 0 else 0.0,
        "pending_invites": pending_invites,
    }


@router.get("/retention")
async def retention_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """User retention by account status."""
    statuses = await db.execute(
        select(User.account_status, func.count()).group_by(User.account_status)
    )
    by_status = {row[0]: row[1] for row in statuses.all()}

    total = sum(by_status.values())

    return {
        "total": total,
        "active": by_status.get("active", 0),
        "invited": by_status.get("invited", 0),
        "deactivated": by_status.get("deactivated", 0),
        "pending_deletion": by_status.get("pending_deletion", 0),
        "active_rate": round(
            by_status.get("active", 0) / total, 2
        ) if total > 0 else 0.0,
    }


@router.get("/checkin")
async def checkin_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Member summary metrics."""
    from app.models.medication import Medication
    from app.models.trusted_contact import TrustedContact

    active_meds = await db.execute(
        select(func.count()).select_from(Medication).where(
            Medication.is_active.is_(True)
        )
    )
    active_caregivers = await db.execute(
        select(func.count()).select_from(TrustedContact).where(
            TrustedContact.is_active.is_(True)
        )
    )

    return {
        "active_medications": active_meds.scalar_one(),
        "active_caregivers": active_caregivers.scalar_one(),
    }


@router.get("/documents")
async def document_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Document processing metrics."""
    total_result = await db.execute(
        select(func.count()).select_from(Document)
    )
    by_status = await db.execute(
        select(Document.status, func.count()).group_by(Document.status)
    )
    by_class = await db.execute(
        select(Document.classification, func.count())
        .where(Document.classification.isnot(None))
        .group_by(Document.classification)
    )

    return {
        "total": total_result.scalar_one(),
        "by_status": {str(row[0]): row[1] for row in by_status.all()},
        "by_classification": {str(row[0]): row[1] for row in by_class.all()},
    }
