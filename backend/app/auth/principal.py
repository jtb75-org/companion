"""Unified principal resolution for the Firebase->Authentik DUAL-RUN switch.

This is the request-time counterpart to ``app/api/auth_authentik.py`` (which mints
the BFF session). Here we CONSUME that session on existing endpoints, controlled by
``settings.authentik_login_enabled``:

  * auth_provider == "firebase" (DEFAULT): ``resolve_session_subject`` short-circuits
    to ``None`` on its first line, so every dependency falls through to its existing
    Firebase bearer path UNCHANGED — byte-identical to the pre-dual-run behavior.
  * auth_provider == "authentik": a valid ``companion_sid`` cookie is resolved to the
    opaque Authentik subject and the member ``User`` is looked up by
    ``external_subject_id`` (RLS-bootstrapped via the login-subject GUC, exactly like
    auth_authentik.login). A Firebase bearer is still accepted as a fallback when no
    session cookie is present, so no client is locked out mid-migration.

Invite-only is already enforced at ``/auth/login`` (a session only exists for a member
whose row pre-existed), so a live session that maps to NO member row is an anomaly →
401. There is no auto-provision here.

CSRF: a session cookie is an ambient/automatic credential, so once it authenticates a
STATE-CHANGING request we enforce the double-submit CSRF check (X-CSRF-Token header ==
companion_csrf cookie). Firebase bearer requests are not cookie-ambient and are not
subject to this check.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import get_session_store
from app.config import settings
from app.db.context import set_login_subject_context, set_user_context
from app.models.user import User

# Mirrors the Firebase path (get_current_user) and auth_authentik.login.
_INACTIVE_STATUSES = ("deactivated", "pending_deletion")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class SessionPrincipal:
    """The member resolved from a valid Authentik BFF session."""

    user: User
    email: str
    subject: str


def _enforce_csrf(request: Request) -> None:
    """Double-submit CSRF for a session-authenticated unsafe (state-changing) method.

    Compares the ``X-CSRF-Token`` header against the non-httpOnly ``companion_csrf``
    cookie set at login. Safe/idempotent methods are exempt (they don't mutate state).
    Firebase bearer requests never reach here (they resolve to ``None`` above)."""
    if request.method in _SAFE_METHODS:
        return
    header = request.headers.get("x-csrf-token") or ""
    cookie = request.cookies.get(settings.csrf_cookie_name) or ""
    if not header or not cookie or not secrets.compare_digest(header, cookie):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


async def resolve_session_subject(request: Request) -> str | None:
    """Return the Authentik subject for a valid BFF session, else ``None``.

    ``None`` means "no Authentik session — fall back to the Firebase bearer path".
    Returns ``None`` when:
      * the dual-run switch is off (auth_provider != "authentik") — the branch is
        inert; this is the first check so the Firebase default is untouched, OR
      * no ``companion_sid`` cookie is present, OR
      * the cookie does not map to a live session (expired / logged out).

    When the switch is on AND a valid session IS present, the double-submit CSRF check
    is enforced for state-changing methods before the subject is returned."""
    if not settings.authentik_login_enabled:
        return None
    sid = request.cookies.get(settings.session_cookie_name)
    if not sid:
        return None
    subject = await get_session_store().get(sid)
    if not subject:
        return None
    _enforce_csrf(request)
    return subject


async def resolve_session_principal(
    request: Request,
    db: AsyncSession,
    *,
    allow_inactive: bool = False,
) -> SessionPrincipal | None:
    """Resolve the member ``User`` from a BFF session, or ``None`` to fall back to
    Firebase.

    Reuses the exact by-subject / login-subject-GUC bootstrap from
    ``auth_authentik.login`` so the RLS ``users`` policy (migration 036) admits the
    pre-context read. Sets the tenant GUC (``app.current_user_id``) for the rest of
    the request, mirroring ``set_user_context`` in the Firebase path.

    ``allow_inactive`` mirrors ``get_current_user_allow_inactive`` (reactivation /
    cancel-deletion): when False, deactivated/pending_deletion accounts are refused
    exactly like the Firebase path."""
    subject = await resolve_session_subject(request)
    if subject is None:
        return None

    # RLS bootstrap: the by-subject read runs before the tenant GUC exists, so set the
    # login-subject GUC first (users policy admits a row whose external_subject_id ==
    # this GUC). Read-only bootstrap; writes stay fenced to the tenant id GUC.
    await set_login_subject_context(db, subject)
    user = (
        await db.execute(select(User).where(User.external_subject_id == subject))
    ).scalar_one_or_none()
    if user is None:
        # A live session that maps to no member row can only happen if the row was
        # deleted after login (or a subject was un-backfilled). Invite-only means we
        # never auto-provision here — treat as an authentication anomaly.
        raise HTTPException(status_code=401, detail="Session does not map to a known member")
    if not allow_inactive and user.account_status in _INACTIVE_STATUSES:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    # Tenant context for the rest of the request (same as the Firebase path).
    await set_user_context(db, user.id)
    return SessionPrincipal(user=user, email=user.email, subject=subject)
