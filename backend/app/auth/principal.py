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

MOBILE bearer session: a React Native client cannot use the httpOnly cookie, so it
presents the SAME opaque session id as ``Authorization: Bearer <session_token>``. Being
non-ambient (a bearer can't be attached cross-site by a browser), it needs NO CSRF, just
like a Firebase bearer. The bearer path is tried ONLY when the switch is on and there is
no valid session cookie. The session store holds opaque ``token_urlsafe`` ids, so a
Firebase id_token (a dotted JWT) simply misses the lookup → ``None`` → the caller falls
through to the existing Firebase-bearer verification. A Firebase JWT is therefore never
mis-resolved as a session, and Firebase verification is untouched.
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
from app.db.session import maintenance_session
from app.models.admin_user import AdminUser
from app.models.trusted_contact import TrustedContact
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


def _bearer_session_token(request: Request) -> str | None:
    """Extract an ``Authorization: Bearer <token>`` value, or ``None``.

    The value may be a Companion session id (opaque ``token_urlsafe``) OR a Firebase
    id_token (a dotted JWT). We do not distinguish here — the session-store lookup does:
    a JWT is not a session key and simply misses, so it falls through to Firebase."""
    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ").strip()
    return token or None


async def resolve_session_subject(request: Request) -> str | None:
    """Return the Authentik subject for a valid BFF session, else ``None``.

    ``None`` means "no Authentik session — fall back to the Firebase bearer path".
    Returns ``None`` when:
      * the dual-run switch is off (auth_provider != "authentik") — the branch is
        inert; this is the first check so the Firebase default is untouched, OR
      * neither a valid ``companion_sid`` cookie NOR a bearer session token is present, OR
      * neither credential maps to a live session (expired / logged out / a Firebase JWT).

    Two credential shapes carry the same opaque session id:
      * COOKIE (``companion_sid``) — ambient, so a state-changing method must also pass
        the double-submit CSRF check before the subject is returned.
      * BEARER (``Authorization: Bearer``) — non-ambient (a browser can't attach it
        cross-site), tried only when there is NO valid session cookie, and — like a
        Firebase bearer — NOT subject to CSRF.

    The bearer lookup runs only when the switch is on, a bearer is present, and no valid
    session cookie resolved, so the extra store lookup is off the hot path. A Firebase
    id_token presented as the bearer is not a session key → misses → ``None`` → the
    caller uses the existing Firebase verification. Firebase JWTs are never mis-resolved
    and Firebase verification is not weakened."""
    if not settings.authentik_login_enabled:
        return None
    store = get_session_store()
    # 1) Cookie session (ambient → CSRF-enforced on unsafe methods).
    sid = request.cookies.get(settings.session_cookie_name)
    if sid:
        subject = await store.get(sid)
        if subject:
            _enforce_csrf(request)
            return subject
    # 2) Bearer session (non-ambient → no CSRF). Only reached when no valid cookie
    #    session resolved. A Firebase JWT misses the store lookup and returns None.
    token = _bearer_session_token(request)
    if token:
        subject = await store.get(token)
        if subject:
            return subject
    return None


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


async def resolve_caregiver_session(request: Request) -> str | None:
    """Return the IdP-verified EMAIL for a caregiver's BFF session, else ``None``.

    The caregiver auth path (app/api/caregiver/*, /api/v1/auth/my-charges) is keyed on
    the verified email — ``trusted_contacts.contact_email`` — NOT a ``users`` row (a
    pure caregiver has none). This is the caregiver counterpart to
    ``resolve_session_principal``; it returns just the email so the existing
    ``caregiver_authorized_for_member(email, user_id)`` gate is unchanged.

    ``None`` means "no Authentik caregiver session — fall back to the Firebase bearer
    path". Returns ``None`` when the switch is off (inert; first line), there is no
    session (``resolve_session_subject``), or the subject maps to neither a member nor
    an active caregiver.

    Resolution runs on the MAINTENANCE (BYPASSRLS) session because caregiver auth runs
    before any member GUC exists and ``trusted_contacts`` is under per-member RLS (030),
    so a normal app-role read would fail closed. We resolve the subject to an email via:
      1) a ``users`` row by subject — a member who is ALSO a caregiver — else
      2) an ACTIVE ``trusted_contacts`` row by subject — a pure caregiver.
    The subject was bound at ``/auth/login`` only after ``email_verified``, so the
    returned email is IdP-verified. CSRF on state-changing methods is already enforced
    inside ``resolve_session_subject`` for cookie sessions."""
    if not settings.authentik_login_enabled:
        return None
    subject = await resolve_session_subject(request)
    if subject is None:
        return None
    async with maintenance_session() as mdb:
        member_email = (
            await mdb.execute(
                select(User.email).where(User.external_subject_id == subject)
            )
        ).scalar_one_or_none()
        if member_email:
            return member_email.strip().lower()
        caregiver_email = (
            await mdb.execute(
                select(TrustedContact.contact_email)
                .where(
                    TrustedContact.external_subject_id == subject,
                    TrustedContact.is_active.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return caregiver_email.strip().lower() if caregiver_email else None


async def resolve_admin_session(request: Request) -> str | None:
    """Return the IdP-verified EMAIL for an admin's BFF session, else ``None``.

    The admin auth path (``get_current_admin``) keys on the verified email, then looks
    up ``admin_users`` by it. Admins are NOT members, so — like caregivers — they may
    have no ``users`` row; this resolver recovers the email from the opaque session
    subject without one. Counterpart to ``resolve_session_principal``, returning just
    the email so the existing ``admin_users`` lookup + active check are unchanged.

    ``None`` means "no Authentik admin session — fall back to the Firebase bearer path".
    Inert when the switch is off (first line). Resolution runs on the MAINTENANCE
    (BYPASSRLS) session so a ``users`` by-subject read is not RLS-fenced; ``admin_users``
    itself is RLS-disabled. We resolve the subject to an email via:
      1) a ``users`` row by subject — an admin who is ALSO a member — else
      2) an ``admin_users`` row by subject — a pure admin.
    The subject was bound at ``/auth/login`` only after ``email_verified``, so the
    returned email is IdP-verified. CSRF on unsafe methods is enforced inside
    ``resolve_session_subject`` for cookie sessions."""
    if not settings.authentik_login_enabled:
        return None
    subject = await resolve_session_subject(request)
    if subject is None:
        return None
    async with maintenance_session() as mdb:
        member_email = (
            await mdb.execute(
                select(User.email).where(User.external_subject_id == subject)
            )
        ).scalar_one_or_none()
        if member_email:
            return member_email.strip().lower()
        admin_email = (
            await mdb.execute(
                select(AdminUser.email).where(
                    AdminUser.external_subject_id == subject
                )
            )
        ).scalar_one_or_none()
        return admin_email.strip().lower() if admin_email else None
