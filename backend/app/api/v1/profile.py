"""Profile completion endpoint."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from app.auth.firebase import verify_firebase_token
from app.auth.principal import resolve_session_principal

log = logging.getLogger(__name__)
from app.config import settings
from app.db import get_db
from app.db.context import set_login_email_context, set_user_context
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User

router = APIRouter(tags=["Profile"])


@router.get("/api/v1/me")
async def get_my_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get current user's profile. Any authenticated member can call this.

    DUAL-RUN: accepts a BFF Authentik session when auth_provider == "authentik",
    else the existing Firebase bearer path (byte-identical under "firebase")."""
    # TEMP DIAGNOSTIC (chore/me-auth-diagnostics): pin down the mobile /me-401. Logs NO
    # token values — only presence + resolution outcome. Remove once the cause is known.
    _bearer = bool(authorization and authorization.startswith("Bearer "))
    _cookie = settings.session_cookie_name in request.cookies
    try:
        principal = await resolve_session_principal(request, db)
    except HTTPException as _e:
        log.info(
            "ME_DIAG provider=%s bearer=%s cookie=%s -> raised %s:%s",
            settings.auth_provider, _bearer, _cookie, _e.status_code, _e.detail,
        )
        raise
    log.info(
        "ME_DIAG provider=%s bearer=%s cookie=%s -> principal=%s",
        settings.auth_provider, _bearer, _cookie,
        "session" if principal is not None else "none(->firebase)",
    )
    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    if principal is not None:
        # Session resolves the member by subject and already set the tenant GUC.
        user = principal.user
    else:
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

        # RLS bootstrap: this endpoint doesn't use get_current_user, so set the
        # login-email GUC so the users policy admits the by-email lookup (Phase 2).
        await set_login_email_context(db, email)
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            return {"exists": False, "profile_complete": False}

        # Now the user id is known, set the tenant GUC so the encrypted-phone DEK
        # row (user_encryption_keys is under per-user RLS) is visible; without it
        # get_user_phone would fail closed as "no DEK row".
        await set_user_context(db, user.id)

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

    DUAL-RUN: accepts a BFF Authentik session when auth_provider == "authentik",
    else the existing Firebase bearer path (byte-identical under "firebase")."""
    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    principal = await resolve_session_principal(request, db)
    if principal is not None:
        # Session resolves the member by subject and already set the tenant GUC.
        user = principal.user
    else:
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

        # RLS bootstrap: this endpoint doesn't use get_current_user, so set the
        # login-email GUC so the users policy admits the by-email lookup (Phase 2).
        await set_login_email_context(db, email)
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            return {"caregivers": []}

        # Set the tenant GUC now that the member id is known: this route reads the
        # member's own trusted_contacts (and will need it once trusted_contacts RLS
        # lands next), so scope the session to this member.
        await set_user_context(db, user.id)

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

    DUAL-RUN: accepts a BFF Authentik session when auth_provider == "authentik"
    (a session only exists for an already-invited member, so the invite-only gate
    is satisfied upstream), else the existing Firebase bearer path (byte-identical
    under "firebase"). As a state-changing POST, the session path enforces the
    double-submit CSRF check inside resolve_session_principal."""
    # Dev bypass
    if settings.dev_auth_bypass and not authorization:
        return {"completed": True}

    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    principal = await resolve_session_principal(request, db)
    if principal is not None:
        # Session resolves the member by subject (invite-only already enforced at
        # /auth/login) and already set the tenant GUC.
        user = principal.user
        email = principal.email
    else:
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

        # RLS bootstrap: this endpoint doesn't use get_current_user, so set the
        # login-email GUC so the users policy admits the by-email lookup (Phase 2).
        await set_login_email_context(db, email)
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        # Invite-only: every invited or admin-provisioned account already has a
        # (stub) User row created at invite time. No row means this email was
        # never invited, so we refuse to self-provision an account here.
        if user is None:
            # Audit the refused signup. Commit it on its own so it survives the
            # request rollback triggered by the 403 below.
            db.add(AccountAuditLog(event="signup_refused", email=email))
            await db.commit()
            raise HTTPException(
                status_code=403,
                detail="No invitation found for this account.",
            )

        # Now that the invited stub is resolved, set the tenant GUC to its id so the
        # activation UPDATE + the encrypted-phone DEK create pass WITH CHECK under
        # RLS (this is the onboarding write path; it never runs get_current_user).
        await set_user_context(db, user.id)

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
