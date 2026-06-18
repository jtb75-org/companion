"""Profile completion endpoint."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.firebase import verify_firebase_token
from app.config import settings
from app.db import get_db
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User

router = APIRouter(tags=["Profile"])


@router.get("/api/v1/me")
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get current user's profile. Any authenticated Firebase user can call this."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "No token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(401, str(e)) from None

    email = decoded.get("email")
    if not email:
        raise HTTPException(401, "No email")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        return {"exists": False, "profile_complete": False}

    return {
        "exists": True,
        "profile_complete": bool(user.first_name and user.last_name),
        "user_id": str(user.id),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "preferred_name": user.preferred_name,
        "display_name": user.display_name,
        "phone": user.phone,
    }


@router.get("/api/v1/me/caregivers")
async def get_my_caregivers(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get the current member's caregivers."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "No token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(401, str(e)) from None

    email = decoded.get("email")
    if not email:
        raise HTTPException(401, "No email")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        return {"caregivers": []}

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
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Complete user profile with first name, last name, phone."""
    # Dev bypass
    if settings.dev_auth_bypass and not authorization:
        return {"completed": True}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "No token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(401, str(e)) from None

    email = decoded.get("email")
    if not email:
        raise HTTPException(401, "No email")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Invite-only: every invited or admin-provisioned account already has a
    # (stub) User row created at invite time. No row means this email was
    # never invited, so we refuse to self-provision an account here.
    if user is None:
        raise HTTPException(
            status_code=403,
            detail="No invitation found for this account.",
        )

    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    phone = data.get("phone") or None
    display = f"{first_name} {last_name}".strip() or email

    user.first_name = first_name or user.first_name
    user.last_name = last_name or user.last_name
    if phone:
        user.phone = phone
    if data.get("preferred_name"):
        user.preferred_name = data["preferred_name"]
    user.display_name = display
    # Completing the profile activates an invited stub account.
    if user.account_status == AccountStatus.INVITED:
        user.account_status = AccountStatus.ACTIVE

    await db.flush()
    return {"completed": True, "user_id": str(user.id)}
