"""Auth check endpoint — called by web dashboard after Firebase login."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorize import authorize_by_email
from app.auth.firebase import verify_firebase_token
from app.auth.principal import resolve_session_email
from app.config import settings
from app.db import get_db
from app.db.context import set_login_email_context
from app.models.user import User as UserModel

router = APIRouter(tags=["Auth"])


@router.get("/api/v1/auth/check")
async def check_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Check authorization for the current user.

    DUAL-RUN: accepts a BFF Authentik session when auth_provider == "authentik"
    (email is the session member's verified email), else the existing Firebase
    bearer path (byte-identical under "firebase")."""

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

    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    # This is a GET, so the CSRF check inside resolve_session_subject is a no-op.
    #
    # Resolve the session's email ROLE-AGNOSTICALLY. This endpoint exists to answer
    # "who is this and what may they do?" for EVERY cohort, and `authorize_by_email`
    # below is the part that knows about admins and caregivers. It must therefore not be
    # gated behind a member lookup: `resolve_session_principal` is MEMBER-ONLY and raises
    # 401 for any subject with no `users` row, so it rejected pure admins and pure
    # caregivers outright — before `authorize_by_email` could ever recognise them. That
    # locked the admin cohort out of the web dashboard at the Authentik cutover (login
    # returned 200, then every /auth/check 401'd with "Session does not map to a known
    # member"). The rest of this handler already expects a member-less caller: it sets its
    # own login-email GUC and reports has_account=False / profile_complete=True for them.
    email = await resolve_session_email(request)
    if email is None:
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
    # RLS bootstrap: set the login-email GUC so the users policy admits this
    # by-email lookup (this route doesn't use get_current_user). Without it a
    # real member fail-closes to no rows and is misrouted as having no account.
    await set_login_email_context(db, email)
    user_result = await db.execute(
        select(UserModel).where(UserModel.email == email)
    )
    user_record = user_result.scalar_one_or_none()

    response["profile_complete"] = user_record is None or bool(
        user_record.first_name and user_record.last_name
    )
    response["has_account"] = user_record is not None

    return response
