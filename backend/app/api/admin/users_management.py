"""Admin API — Companion Users management."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.integrations.authentik_admin import provision_authentik_account
from app.integrations.email_service import (
    send_account_deactivated,
    send_account_deleted_to_caregiver,
    send_account_reactivated,
    send_caregiver_access_revoked,
    send_deletion_cancelled,
    send_deletion_requested,
)
from app.models.enums import AccountStatus, DeletionReason
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.services.account_lifecycle_service import (
    cancel_deletion,
    deactivate_account,
    execute_deletion,
    reactivate_account,
    request_deletion,
)
from app.services.activation_service import send_activation_if_enabled

_editor = require_admin_role("editor")

router = APIRouter(tags=["Admin - Users"])


@router.get("/admin/companion-users")
async def list_companion_users(
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """List all companion users with full details."""
    from app.services.field_crypto import decrypt_row_field

    result = await db.execute(select(User).order_by(User.first_name, User.last_name))
    users = result.scalars().all()
    out = []
    for u in users:
        out.append({
            "id": str(u.id),
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "phone": await decrypt_row_field(db, u, "phone"),
            "preferred_name": u.preferred_name,
            "display_name": u.display_name,
            "account_status": u.account_status,
            "care_model": u.care_model,
            "deactivated_at": u.deactivated_at.isoformat() if u.deactivated_at else None,
            "deletion_scheduled_at": (
                u.deletion_scheduled_at.isoformat() if u.deletion_scheduled_at else None
            ),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return {"users": out}


@router.post("/admin/companion-users", status_code=status.HTTP_201_CREATED)
async def create_companion_user(
    data: dict,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Create a new companion user."""
    result = await db.execute(select(User).where(User.email == data.get("email")))
    if result.scalar_one_or_none():
        raise HTTPException(409, "User with this email already exists")

    first = data.get("first_name", "")
    last = data.get("last_name", "")
    user = User(
        email=data["email"],
        first_name=first,
        last_name=last,
        preferred_name=data.get("preferred_name", first),
        display_name=f"{first} {last}".strip() or data["email"],
        primary_language="en",
        voice_id="warm",
        pace_setting="normal",
        warmth_level="warm",
    )
    db.add(user)
    await db.flush()  # assigns user.id for the per-tenant DEK binding
    if data.get("phone"):
        from app.services.field_crypto import set_user_profile_pii
        await set_user_profile_pii(db, user, phone=data["phone"])
        await db.flush()
    # Provision a matching Authentik account (branded BFF provisioning, PR 1).
    # Commit first so a provisioning error can never roll back the row; the call
    # is best-effort + idempotent + inert on the Firebase default.
    await db.commit()
    _name = user.display_name or user.email
    await provision_authentik_account(user.email, _name)
    # Under Authentik, email the branded activation link (best-effort, inert under
    # firebase). The /activate universal link opens the mobile app for members.
    await send_activation_if_enabled(user.email, _name)
    return {"id": str(user.id), "created": True}


@router.patch("/admin/companion-users/{user_id}")
async def update_companion_user(
    user_id: uuid.UUID,
    data: dict,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Update a companion user."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    for field in ["first_name", "last_name", "preferred_name", "email"]:
        if field in data:
            setattr(user, field, data[field])

    if "phone" in data:
        # phone is encrypted at rest (per-tenant envelope).
        from app.services.field_crypto import set_user_profile_pii
        await set_user_profile_pii(db, user, phone=data["phone"])

    if "first_name" in data or "last_name" in data:
        first = data.get("first_name", user.first_name) or ""
        last = data.get("last_name", user.last_name) or ""
        user.display_name = f"{first} {last}".strip() or user.email

    await db.flush()
    return {"updated": True}


async def _get_caregiver_contacts(db: AsyncSession, user_id: uuid.UUID):
    """Get caregiver email/name pairs for notifications."""
    result = await db.execute(
        select(TrustedContact.contact_email, TrustedContact.contact_name).where(
            TrustedContact.user_id == user_id,
            TrustedContact.contact_email.isnot(None),
        )
    )
    return result.all()


@router.post("/admin/companion-users/{user_id}/deactivate")
async def admin_deactivate_user(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Admin deactivates a user account."""
    try:
        user = await deactivate_account(db, user_id, initiated_by=f"admin:{admin.email}")
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    name = user.preferred_name or user.display_name
    await send_account_deactivated(user.email, name)
    for email, cname in await _get_caregiver_contacts(db, user_id):
        await send_caregiver_access_revoked(email, cname, name, "deactivated")

    return {"deactivated": True}


@router.post("/admin/companion-users/{user_id}/reactivate")
async def admin_reactivate_user(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Admin reactivates a user account."""
    try:
        user = await reactivate_account(db, user_id, initiated_by=f"admin:{admin.email}")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    await send_account_reactivated(user.email, user.preferred_name or user.display_name)
    return {"reactivated": True}


@router.post("/admin/companion-users/{user_id}/request-deletion")
async def admin_request_deletion(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Admin requests account deletion with 30-day grace period."""
    try:
        result = await request_deletion(
            db, user_id, DeletionReason.ADMIN_REQUEST, initiated_by=f"admin:{admin.email}"
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    if isinstance(result, dict):
        return {"deleted": True, "immediate": True}

    name = result.preferred_name or result.display_name
    scheduled = (
        result.deletion_scheduled_at.strftime("%B %d, %Y")
        if result.deletion_scheduled_at else "30 days"
    )
    await send_deletion_requested(result.email, name, scheduled)
    return {"deletion_requested": True, "scheduled_date": scheduled}


@router.post("/admin/companion-users/{user_id}/cancel-deletion")
async def admin_cancel_deletion(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Admin cancels pending deletion."""
    try:
        user = await cancel_deletion(db, user_id, initiated_by=f"admin:{admin.email}")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    await send_deletion_cancelled(user.email, user.preferred_name or user.display_name)
    return {"deletion_cancelled": True}


@router.delete("/admin/companion-users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_companion_user(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Permanently delete a user. Requires pending_deletion status or forces immediate deletion."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if user.account_status not in (AccountStatus.PENDING_DELETION, AccountStatus.DEACTIVATED):
        raise HTTPException(
            400,
            "Use the request-deletion endpoint first, or deactivate the account. "
            "Direct deletion is only allowed for deactivated or pending-deletion accounts.",
        )

    name = user.preferred_name or user.display_name
    caregivers = await _get_caregiver_contacts(db, user_id)

    await execute_deletion(db, user_id)

    # Notify caregivers
    for email, cname in caregivers:
        await send_account_deleted_to_caregiver(email, cname, name)

    return None
