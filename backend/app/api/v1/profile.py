"""Profile completion endpoint."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import resolve_session_principal
from app.config import settings
from app.db import get_db
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact

router = APIRouter(tags=["Profile"])


@router.get("/api/v1/me")
async def get_my_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get current user's profile. Any authenticated member can call this.

    Resolved from the Authentik BFF session."""
    principal = await resolve_session_principal(request, db)
    if principal is None:
        raise HTTPException(401, "Not authenticated")
    # Session resolves the member by subject and already set the tenant GUC.
    user = principal.user

    from app.services.field_crypto import get_user_phone
    return {
        "exists": True,
        "profile_complete": bool(user.first_name and user.last_name),
        "user_id": str(user.id),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "preferred_name": user.preferred_name,
        "display_name": user.display_name,
        "phone": await get_user_phone(db, user),
    }


@router.get("/api/v1/me/caregivers")
async def get_my_caregivers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get the current member's caregivers.

    Resolved from the Authentik BFF session."""
    principal = await resolve_session_principal(request, db)
    if principal is None:
        raise HTTPException(401, "Not authenticated")
    # Session resolves the member by subject and already set the tenant GUC.
    user = principal.user

    contacts_result = await db.execute(
        select(TrustedContact).where(TrustedContact.user_id == user.id)
    )
    contacts = contacts_result.scalars().all()

    return {
        "caregivers": [
            {
                "id": str(c.id),
                "contact_name": c.contact_name,
                "contact_email": c.contact_email,
                "relationship_type": c.relationship_type,
                "access_tier": c.access_tier,
                "invitation_status": c.invitation_status or "accepted",
                "is_active": c.is_active,
            }
            for c in contacts
        ]
    }


@router.post("/api/v1/auth/complete-profile")
async def complete_profile(
    data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Complete user profile with first name, last name, phone.

    Resolved from the Authentik BFF session (a session only exists for an
    already-invited member, so the invite-only gate is satisfied upstream). As a
    state-changing POST, the session path enforces the double-submit CSRF check inside
    resolve_session_principal."""
    # Dev bypass
    if settings.dev_auth_bypass and not authorization:
        return {"completed": True}

    principal = await resolve_session_principal(request, db)
    if principal is None:
        raise HTTPException(401, "Not authenticated")
    # Session resolves the member by subject (invite-only already enforced at
    # /auth/login) and already set the tenant GUC.
    user = principal.user
    email = principal.email

    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    phone = data.get("phone") or None
    display = f"{first_name} {last_name}".strip() or email

    user.first_name = first_name or user.first_name
    user.last_name = last_name or user.last_name
    if phone:
        # phone is encrypted at rest (per-tenant envelope). The user row
        # already exists (fetched above), so its id is available for the DEK.
        from app.services.field_crypto import set_user_profile_pii
        await set_user_profile_pii(db, user, phone=phone)
    if data.get("preferred_name"):
        user.preferred_name = data["preferred_name"]
    user.display_name = display
    # Completing the profile activates an invited stub account.
    if user.account_status == AccountStatus.INVITED:
        user.account_status = AccountStatus.ACTIVE
        db.add(
            AccountAuditLog(
                event="account_activated", email=email, user_id=user.id
            )
        )

    await db.flush()
    return {"completed": True, "user_id": str(user.id)}
