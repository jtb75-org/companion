"""Caregiver API — Activity Log."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorize import caregiver_authorized_for_member
from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db import get_db
from app.db.context import set_user_context
from app.services import caregiver_service

router = APIRouter(tags=["Caregiver"])


@router.get("/activity")
async def get_activity(
    request: Request,
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

    # Resolve the caregiver's verified email from the Authentik BFF session.
    email = await resolve_caregiver_session(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
