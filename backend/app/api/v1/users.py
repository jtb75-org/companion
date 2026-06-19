"""App API — User profile and memory routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import User, get_current_user, get_current_user_allow_inactive
from app.db import get_db
from app.integrations.email_service import (
    send_account_deactivated,
    send_account_reactivated,
    send_caregiver_access_revoked,
    send_deletion_cancelled,
    send_deletion_requested,
)
from app.models.enums import CareModel, DeletionReason
from app.models.trusted_contact import TrustedContact
from app.schemas.user import UserUpdate
from app.services import caregiver_service, memory_service
from app.services.account_lifecycle_service import (
    cancel_deletion,
    deactivate_account,
    reactivate_account,
    request_deletion,
)
from app.services.field_crypto import (
    get_user_address,
    get_user_date_of_birth,
    get_user_phone,
    set_user_profile_pii,
)

router = APIRouter(prefix="/me", tags=["Users"])

# Profile attributes that are encrypted at rest (per-tenant envelope). These
# must never be written via a raw setattr nor serialized straight off the ORM
# row — encryption/decryption is routed through field_crypto helpers.
_ENCRYPTED_PROFILE_ATTRS = ("date_of_birth", "phone", "address")


async def _serialize_user(db: AsyncSession, user: User) -> dict:
    """Serialize the current user, decrypting the per-tenant encrypted fields.

    Returning the raw ORM row would leak tagged ciphertext (``f2:``) for the
    encrypted profile columns. Mirrors profile.py::get_my_profile.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "preferred_name": user.preferred_name,
        "display_name": user.display_name,
        "nickname": user.nickname,
        "phone": await get_user_phone(db, user),
        "date_of_birth": await get_user_date_of_birth(db, user),
        "address": await get_user_address(db, user),
        "primary_language": user.primary_language,
        "voice_id": user.voice_id,
        "pace_setting": user.pace_setting,
        "warmth_level": user.warmth_level,
        "quiet_start": user.quiet_start.isoformat() if user.quiet_start else None,
        "quiet_end": user.quiet_end.isoformat() if user.quiet_end else None,
        "checkin_time": user.checkin_time.isoformat() if user.checkin_time else None,
        "away_mode": user.away_mode,
        "account_status": user.account_status,
        "care_model": user.care_model,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("")
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current user profile."""
    return await _serialize_user(db, user)


@router.patch("")
async def update_profile(
    data: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update profile/preferences."""
    updates = data.model_dump(exclude_unset=True)
    # Encrypted PHI fields must NOT be written via the generic setattr loop —
    # that would store raw plaintext into the now-encrypted columns and corrupt
    # the read path. Route them through set_user_profile_pii instead.
    pii_kwargs = {
        attr: updates.pop(attr)
        for attr in _ENCRYPTED_PROFILE_ATTRS
        if attr in updates
    }
    for key, value in updates.items():
        setattr(user, key, value)
    if pii_kwargs:
        await set_user_profile_pii(db, user, **pii_kwargs)
    await db.flush()
    return await _serialize_user(db, user)


@router.get("/memory")
async def list_memories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List functional memories."""
    from app.services.field_crypto import decrypt_row_field

    memories = await memory_service.list_memories(db, user.id)
    items = []
    for m in memories:
        items.append({
            "id": str(m.id),
            "category": m.category,
            "key": m.key,
            "value": await decrypt_row_field(db, m, "value"),
            "source": m.source,
        })
    return {"memories": items, "total": len(items)}


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a specific memory."""
    deleted = await memory_service.delete_memory(db, user.id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return None


@router.post("/deactivate")
async def deactivate_my_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate own account."""
    await deactivate_account(db, user.id, initiated_by="user")

    # Notify user
    await send_account_deactivated(user.email, user.preferred_name or user.display_name)

    # Notify caregivers
    from sqlalchemy import select
    result = await db.execute(
        select(TrustedContact.contact_email, TrustedContact.contact_name).where(
            TrustedContact.user_id == user.id,
            TrustedContact.contact_email.isnot(None),
        )
    )
    for email, name in result.all():
        await send_caregiver_access_revoked(
            email, name, user.preferred_name or user.display_name, "deactivated"
        )

    return {"deactivated": True}


@router.post("/reactivate")
async def reactivate_my_account(
    user: User = Depends(get_current_user_allow_inactive),
    db: AsyncSession = Depends(get_db),
):
    """Reactivate own account from deactivated state."""
    try:
        await reactivate_account(db, user.id, initiated_by="user")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await send_account_reactivated(user.email, user.preferred_name or user.display_name)
    return {"reactivated": True}


@router.post("/request-deletion")
async def request_my_deletion(
    user: User = Depends(get_current_user_allow_inactive),
    db: AsyncSession = Depends(get_db),
):
    """Request account deletion with 30-day grace period. Blocked for managed accounts."""
    if user.care_model == CareModel.MANAGED:
        raise HTTPException(
            403, "Managed accounts can only be deleted by an administrator."
        )
    result = await request_deletion(db, user.id, DeletionReason.USER_REQUEST, initiated_by="user")
    if isinstance(result, dict):
        # Immediate deletion (grace=0)
        return {"deleted": True, "immediate": True}
    scheduled = (
        result.deletion_scheduled_at.strftime("%B %d, %Y")
        if result.deletion_scheduled_at else "30 days"
    )
    await send_deletion_requested(user.email, user.preferred_name or user.display_name, scheduled)
    return {"deletion_requested": True, "scheduled_date": scheduled}


@router.post("/cancel-deletion")
async def cancel_my_deletion(
    user: User = Depends(get_current_user_allow_inactive),
    db: AsyncSession = Depends(get_db),
):
    """Cancel pending deletion."""
    try:
        await cancel_deletion(db, user.id, initiated_by="user")
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    await send_deletion_cancelled(user.email, user.preferred_name or user.display_name)
    return {"deletion_cancelled": True}


@router.get("/activity")
async def get_activity(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Caregiver activity log visible to the user."""
    activities = await caregiver_service.get_caregiver_activity(db, user.id)
    return {"activities": activities, "total": len(activities)}
