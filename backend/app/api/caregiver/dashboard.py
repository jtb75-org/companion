"""Caregiver API — Dashboard."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorize import authorized_caregiver_contact_id
from app.auth.firebase import verify_firebase_token
from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db import get_db
from app.db.context import set_user_context
from app.models.enums import CaregiverAction
from app.services import caregiver_service

router = APIRouter(tags=["Caregiver"])


@router.get("/dashboard")
async def get_dashboard(
    request: Request,
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

    # DUAL-RUN: prefer an Authentik caregiver session (inert unless auth_provider ==
    # "authentik"); its verified email replaces the Firebase claim. When None, the
    # Firebase bearer path runs UNCHANGED.
    email = await resolve_caregiver_session(request)
    if email is None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No token")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            decoded = await verify_firebase_token(token)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e)) from None
        email = decoded.get("email")

    # Verify this email is an active trusted contact for this member and get the
    # contact id (needed for the audit record). The check runs on the maintenance
    # session (no member GUC yet → RLS would fail closed) and IS the real access gate.
    contact_id = await authorized_caregiver_contact_id(email, user_id)
    if contact_id is None:
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    # Authorized → set the tenant context to the member so the summary's per-member
    # reads (and the audit write below) pass RLS (member-id-as-context, WS1 Phase 2).
    await set_user_context(db, user_id)
    # Transparency (docs §5): log the Tier-2 dashboard view before returning it, in the
    # same transaction as the read — so a returned dashboard always has a committed
    # audit record. Structured metadata only, no member data.
    await caregiver_service.log_caregiver_action(
        db,
        trusted_contact_id=contact_id,
        user_id=user_id,
        action=CaregiverAction.VIEWED_DASHBOARD,
        details={"surface": "dashboard"},
    )
    return await caregiver_service.get_dashboard_summary(db, user_id)
