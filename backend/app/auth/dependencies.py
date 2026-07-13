from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.firebase import verify_firebase_token
from app.auth.principal import resolve_session_principal
from app.config import settings

# Database session dependency — imported from wherever the app defines it.
# This is a placeholder import; adjust to match the actual session provider.
from app.db import get_db
from app.db.context import set_login_email_context, set_user_context
from app.db.session import maintenance_session
from app.models.admin_user import AdminUser
from app.models.enums import AccessTier
from app.models.trusted_contact import TrustedContact
from app.models.user import User


@dataclass
class CaregiverContext:
    contact: TrustedContact
    user_id: uuid.UUID
    tier: AccessTier


# ---------------------------------------------------------------------------
# Helper: extract and verify bearer token
# ---------------------------------------------------------------------------

async def _extract_bearer_token(authorization: str | None) -> dict:
    """Extract Bearer token from header and verify with Firebase."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        decoded = await verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from None
    return decoded


# ---------------------------------------------------------------------------
# App API dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user.

    DUAL-RUN: when auth_provider == "authentik" and a valid BFF session cookie is
    present, the member is resolved from that session (preferred); otherwise the
    existing Firebase bearer path runs. When auth_provider == "firebase" (DEFAULT)
    ``resolve_session_principal`` returns ``None`` immediately and behavior is
    byte-identical to before.

    In development/test environments, if no Authorization header is provided
    the first user in the database is returned as a convenience mock.
    """
    # Dev/test bypass: skip auth when no header is provided
    if (
        settings.dev_auth_bypass
        and authorization is None
    ):
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="No mock user available in dev database")
        await set_user_context(db, user.id)
        return user

    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    principal = await resolve_session_principal(request, db)
    if principal is not None:
        return principal.user

    decoded = await _extract_bearer_token(authorization)

    # Look up by email from Firebase claims
    email: str | None = decoded.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Firebase token missing email claim")

    # RLS bootstrap: the email lookup runs before the user_id is known, so set the
    # login-email GUC first so the `users` policy admits this row (Phase 2).
    await set_login_email_context(db, email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.account_status in ("deactivated", "pending_deletion"):
        raise HTTPException(status_code=403, detail="Account is deactivated")
    # Tenant context for the rest of the request (no-op until RLS policies land).
    await set_user_context(db, user.id)
    return user


async def get_current_user_allow_inactive(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Like get_current_user but allows deactivated/pending_deletion accounts.

    Used only for reactivation and cancel-deletion endpoints. DUAL-RUN aware:
    accepts a BFF session (with allow_inactive) when Authentik is live, else the
    existing Firebase path (unchanged when auth_provider == "firebase").
    """
    if settings.dev_auth_bypass and authorization is None:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="No mock user available in dev database")
        await set_user_context(db, user.id)
        return user

    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    principal = await resolve_session_principal(request, db, allow_inactive=True)
    if principal is not None:
        return principal.user

    decoded = await _extract_bearer_token(authorization)
    email: str | None = decoded.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Firebase token missing email claim")

    await set_login_email_context(db, email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await set_user_context(db, user.id)
    return user


# ---------------------------------------------------------------------------
# Profile-completion gate
# ---------------------------------------------------------------------------

async def require_complete_profile(
    user: User = Depends(get_current_user),
) -> User:
    """Ensure the authenticated user has completed their profile."""
    if not user.first_name or not user.last_name:
        raise HTTPException(
            status_code=403,
            detail="Profile incomplete. Complete your profile first.",
        )
    return user


# ---------------------------------------------------------------------------
# Caregiver API dependency
# ---------------------------------------------------------------------------

async def get_current_caregiver(
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> CaregiverContext:
    """Resolve the authenticated caregiver from a Firebase ID token with
    custom claims (contact_id, user_id, tier).

    In development/test environments, if no Authorization header is provided
    a mock caregiver context is returned using the first trusted contact.
    """
    # Dev/test bypass: skip auth when no header is provided
    if (
        settings.dev_auth_bypass
        and authorization is None
    ):
        result = await db.execute(select(TrustedContact).limit(1))
        contact = result.scalar_one_or_none()
        if contact is None:
            raise HTTPException(
                status_code=404,
                detail="No mock caregiver in dev database",
            )
        await set_user_context(db, contact.user_id)
        return CaregiverContext(
            contact=contact,
            user_id=contact.user_id,
            tier=contact.tier,
        )

    decoded = await _extract_bearer_token(authorization)

    contact_id = decoded.get("contact_id")

    if not contact_id:
        raise HTTPException(
            status_code=401,
            detail="Firebase token missing required caregiver claims",
        )

    # The caregiver's own contact row is looked up by the claim's contact_id
    # before any member GUC exists, so under trusted_contacts RLS this must run
    # on the maintenance (BYPASSRLS) session or it fails closed → caregiver-auth
    # outage. This read authorizes the caller, so keep it narrow (single id).
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(TrustedContact).where(
                TrustedContact.id == uuid.UUID(contact_id)
            )
        )
        contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=401, detail="Trusted contact not found")
    if not contact.is_active:
        raise HTTPException(status_code=403, detail="Trusted contact is not active")
    # Defense in depth: bind the contact row to the verified Firebase email.
    # Caregivers are email-invited so their tokens always carry an email claim;
    # require it (like get_current_user) and fail closed on a malformed token, and
    # reject a stale/mismatched contact_id claim (relationship revoked and the id
    # re-pointed to another member) that no longer matches the caller's identity.
    claim_email = (decoded.get("email") or "").lower()
    if not claim_email:
        raise HTTPException(status_code=401, detail="Caregiver token missing email claim")
    if (contact.contact_email or "").lower() != claim_email:
        raise HTTPException(status_code=403, detail="Caregiver identity mismatch")

    # Caregiver = member-id-as-context: authz happened above (the contact row);
    # RLS then scopes every downstream query on the request session to this
    # member. No caregiver branch in the table policies is needed.
    await set_user_context(db, contact.user_id)
    return CaregiverContext(
        contact=contact,
        user_id=contact.user_id,
        tier=contact.access_tier,
    )


# ---------------------------------------------------------------------------
# Tier enforcement dependency factory
# ---------------------------------------------------------------------------

def require_tier(minimum_tier: AccessTier):
    """Returns a dependency that enforces minimum caregiver tier."""

    async def check(
        caregiver: CaregiverContext = Depends(get_current_caregiver),
    ) -> CaregiverContext:
        tier_order = {
            AccessTier.TIER_1: 1,
            AccessTier.TIER_2: 2,
            AccessTier.TIER_3: 3,
        }
        if tier_order[caregiver.tier] < tier_order[minimum_tier]:
            raise HTTPException(status_code=403, detail="Insufficient access tier")
        return caregiver

    return check


# ---------------------------------------------------------------------------
# Admin API dependency
# ---------------------------------------------------------------------------

async def get_current_admin(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Resolve the authenticated admin.

    DUAL-RUN: when auth_provider == "authentik" and a valid BFF session is present,
    the admin is resolved by the session member's verified email (a session only
    exists for a member whose row pre-existed via /auth/login, so the subject
    resolves to a User row carrying the trusted email). Otherwise the existing
    Firebase bearer path runs. Byte-identical to before when auth_provider ==
    "firebase". ``admin_users`` is RLS-disabled, so no tenant GUC is needed for the
    admin lookup itself.

    In development/test environments, if no Authorization header is provided
    the first admin user in the database is returned as a convenience mock.
    """
    # Dev/test bypass: skip auth when no header is provided
    if (
        settings.dev_auth_bypass
        and authorization is None
    ):
        result = await db.execute(select(AdminUser).limit(1))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise HTTPException(status_code=404, detail="No mock admin available in dev database")
        return admin

    # DUAL-RUN Authentik-session branch (inert unless auth_provider == "authentik").
    principal = await resolve_session_principal(request, db)
    if principal is not None:
        email: str | None = principal.email
    else:
        decoded = await _extract_bearer_token(authorization)
        email = decoded.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Firebase token missing email claim")

    result = await db.execute(select(AdminUser).where(AdminUser.email == email))
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=403, detail="Not an admin user")
    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Admin account is not active")
    return admin


# ---------------------------------------------------------------------------
# Admin role enforcement dependency factory
# ---------------------------------------------------------------------------

_ROLE_ORDER = {"viewer": 1, "editor": 2, "admin": 3}


def require_admin_role(minimum_role: str):
    """Returns a dependency that enforces minimum admin role.

    Role hierarchy: viewer < editor < admin.
    """

    async def check(
        admin: AdminUser = Depends(get_current_admin),
    ) -> AdminUser:
        current_level = _ROLE_ORDER.get(admin.role)
        required_level = _ROLE_ORDER.get(minimum_role)

        if current_level is None:
            raise HTTPException(
                status_code=403,
                detail="Invalid admin role",
            )
        if required_level is None:
            raise HTTPException(
                status_code=500,
                detail="Invalid required role configuration",
            )
        if current_level < required_level:
            raise HTTPException(status_code=403, detail="Insufficient admin role")
        return admin

    return check
