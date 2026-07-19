from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import resolve_admin_session, resolve_session_principal
from app.config import settings

# Database session dependency — imported from wherever the app defines it.
# This is a placeholder import; adjust to match the actual session provider.
from app.db import get_db
from app.db.context import set_user_context
from app.models.admin_user import AdminUser
from app.models.user import User

# ---------------------------------------------------------------------------
# App API dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated member from the Authentik BFF session.

    The member is resolved from the BFF session (``companion_sid`` cookie for web, or
    the same opaque session id as ``Authorization: Bearer`` for mobile) via
    ``resolve_session_principal``. There is no other authentication path — Firebase
    was retired.

    In development/test environments, if no Authorization header is provided
    the first user in the database is returned as a convenience mock.
    """
    # Dev/test bypass: skip auth when no header is provided
    if settings.dev_auth_bypass and authorization is None:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="No mock user available in dev database")
        await set_user_context(db, user.id)
        return user

    principal = await resolve_session_principal(request, db)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal.user


async def get_current_user_allow_inactive(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Like get_current_user but allows deactivated/pending_deletion accounts.

    Used only for reactivation and cancel-deletion endpoints. Resolves the member from
    the Authentik BFF session with ``allow_inactive=True``.
    """
    if settings.dev_auth_bypass and authorization is None:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="No mock user available in dev database")
        await set_user_context(db, user.id)
        return user

    principal = await resolve_session_principal(request, db, allow_inactive=True)
    if principal is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal.user


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
# Admin API dependency
# ---------------------------------------------------------------------------

async def get_current_admin(
    request: Request = None,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Resolve the authenticated admin from the Authentik BFF session.

    The admin is resolved by the session's verified email via ``resolve_admin_session``
    — which recovers the email from the opaque subject whether the admin is also a
    member (``users`` row) or a PURE admin (``admin_users`` only, no ``users`` row).
    ``admin_users`` is RLS-disabled, so no tenant GUC is needed for the admin lookup.

    In development/test environments, if no Authorization header is provided
    the first admin user in the database is returned as a convenience mock.
    """
    # Dev/test bypass: skip auth when no header is provided
    if settings.dev_auth_bypass and authorization is None:
        result = await db.execute(select(AdminUser).limit(1))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise HTTPException(status_code=404, detail="No mock admin available in dev database")
        return admin

    # Resolve the admin's verified email from the BFF session (member OR pure-admin
    # subject). None means no valid session → not authenticated.
    email: str | None = await resolve_admin_session(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
