"""Caregiver API — Activity Log."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorize import caregiver_authorized_for_member
from app.auth.firebase import verify_firebase_token
from app.config import settings
from app.db import get_db
from app.db.context import set_user_context
from app.services import caregiver_service

router = APIRouter(tags=["Caregiver"])


@router.get("/activity")
async def get_activity(
    user_id: uuid.UUID = Query(..., description="User ID to view activity for"),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get activity log for a specific user (charge).

    Requires the caller to be assigned as a trusted contact for this user.
    """
    # Dev bypass
    if settings.dev_auth_bypass and not authorization:
        await set_user_context(db, user_id)
        activity = await caregiver_service.get_caregiver_activity(db, user_id)
        return {"activity": activity}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None

    email = decoded.get("email")

    # Verify this email is an active trusted contact for this member (on the
    # maintenance session — no member GUC yet → RLS would fail closed). This IS
    # the access gate.
    if not await caregiver_authorized_for_member(email, user_id):
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    # Caregiver authorized → set tenant context to the member so RLS scopes the
    # activity read (member-id-as-context, WS1 Phase 2).
    await set_user_context(db, user_id)
    activity = await caregiver_service.get_caregiver_activity(db, user_id)
    return {"activity": activity}
