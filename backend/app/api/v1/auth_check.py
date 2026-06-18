"""Auth check endpoint — called by web dashboard after Firebase login."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorize import authorize_by_email
from app.auth.firebase import verify_firebase_token
from app.config import settings
from app.db import get_db
from app.models.user import User as UserModel

router = APIRouter(tags=["Auth"])


@router.get("/api/v1/auth/check")
async def check_auth(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Check authorization for the current Firebase user."""

    # Dev/test bypass
    if settings.dev_auth_bypass:
        if authorization is None:
            return {
                "authorized": True,
                "role": "admin",
                "admin_role": "admin",
                "email": "dev@companion.app",
                "profile_complete": True,
                "has_account": True,
            }

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token provided")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None

    email = decoded.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="No email in token")

    auth_result = await authorize_by_email(db, email)

    if not auth_result.is_authorized:
        raise HTTPException(
            status_code=403,
            detail="Access denied. Contact your administrator to request access.",
        )

    response = {
        "authorized": True,
        "role": auth_result.role,
        "email": auth_result.email,
    }

    if auth_result.is_admin:
        response["admin_role"] = auth_result.admin_role

    if auth_result.is_caregiver:
        response["caregiver_count"] = len(auth_result.caregiver_contacts)
        response["has_charges"] = len(auth_result.caregiver_contacts) > 0

    # Profile completion only applies to MEMBER accounts. Admins/caregivers
    # without a member row don't need a member profile (and can't create one —
    # signup is invite-only, so complete-profile would 403). Only an existing
    # member account with missing names should be routed to the completion
    # screen.
    user_result = await db.execute(
        select(UserModel).where(UserModel.email == email)
    )
    user_record = user_result.scalar_one_or_none()

    response["profile_complete"] = user_record is None or bool(
        user_record.first_name and user_record.last_name
    )
    response["has_account"] = user_record is not None

    return response
