"""Caregiver API — Dashboard."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.firebase import verify_firebase_token
from app.config import settings
from app.db import get_db
from app.db.context import set_user_context
from app.models.trusted_contact import TrustedContact
from app.services import caregiver_service

router = APIRouter(tags=["Caregiver"])


@router.get("/dashboard")
async def get_dashboard(
    user_id: uuid.UUID = Query(..., description="User ID to view dashboard for"),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Summary dashboard for a specific user (charge).

    Requires the caller to be assigned as a trusted contact for this user.
    """
    # Dev bypass
    if settings.dev_auth_bypass and not authorization:
        # Tenant context so the summary's per-member reads pass RLS (WS1 Phase 2).
        await set_user_context(db, user_id)
        return await caregiver_service.get_dashboard_summary(db, user_id)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None

    email = decoded.get("email")

    # Verify this email is assigned as a trusted contact for this user
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.contact_email == email,
            TrustedContact.user_id == user_id,
            TrustedContact.is_active.is_(True),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    # Caregiver is authorized for this member (the check above is the real gate);
    # set the tenant context to the member so the summary's per-member reads pass
    # RLS (member-id-as-context, WS1 Phase 2). No caregiver branch in the policy.
    await set_user_context(db, user_id)
    return await caregiver_service.get_dashboard_summary(db, user_id)
