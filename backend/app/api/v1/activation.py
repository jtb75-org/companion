"""Generic, email-keyed account-activation routes (branded set-password).

An account is created (an admin now via ``admin_users``; members later via ``users``)
and an activation email carries a single-use token. The token holder validates the
link, sets their Authentik password in Companion's branded UI, then signs in via
``/auth/login``. This surface is generic on purpose — keyed by EMAIL, not a cohort id
— so admin and member activation share one backend.

INERT under ``auth_provider=firebase``: ``set-password`` 404s (``_require_authentik_
enabled``); the token itself is only ever issued from Authentik-gated seams, so on the
Firebase default nothing here is reachable with a real token.

Set-password capability note (for reviewers): a token could in principle target an
account that is ALREADY established. The guard is SINGLE-USE + EXPIRY (migration 040):
a token is consumed on the first successful password set and cannot reset an account
again, and it expires (72h default). Admins have no INVITED/ACTIVE status column to
gate on (unlike the caregiver invite path), so the token lifecycle IS the guard — an
issued token is a one-shot, time-boxed password-set capability delivered only to the
account's own email.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.auth_authentik import _require_authentik_enabled
from app.db.session import maintenance_session
from app.integrations.authentik_admin import (
    provision_authentik_account,
    set_authentik_password,
)
from app.models.admin_user import AdminUser
from app.models.user import User
from app.schemas.activation import ActivationSetPassword
from app.services.activation_service import (
    consume_activation_token,
    release_activation_token,
    resolve_activation_email,
)

log = logging.getLogger("companion.activation")

router = APIRouter(prefix="/activation", tags=["Activation"])


async def _lookup_account_name(email: str) -> str | None:
    """Return a display name for ``email`` from admin_users then users, else None.

    Runs on the maintenance (BYPASSRLS) session: this is pre-auth (no tenant GUC) and
    ``users`` is per-user RLS-fenced, so a normal session would fail-close. ``None``
    means no account exists for the email at all."""
    async with maintenance_session() as mdb:
        admin = (
            await mdb.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if admin is not None:
            return admin.name
        user = (
            await mdb.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is not None:
            return user.preferred_name or user.display_name or email
    return None


@router.get("/validate")
async def validate_activation_token(token: str):
    """Validate an activation token (public, no auth). Powers the /activate landing page.

    Returns the email + a display name for the token holder. Echoing the email back is
    safe: the token was emailed to that address, so its holder already knows it."""
    email = await resolve_activation_email(token)
    if email is None:
        raise HTTPException(404, "Invalid or expired activation link")
    name = await _lookup_account_name(email)
    return {"valid": True, "email": email, "name": name or email}


@router.post("/set-password")
async def set_activation_password(data: ActivationSetPassword):
    """Redeem an activation token and set the holder's Authentik password.

    Authentik-only (404s under firebase). Does NOT mint a session or log the user in —
    the web calls ``/auth/login`` next. The token is consumed only AFTER a successful
    password set, so an IdP failure leaves it usable for a retry."""
    _require_authentik_enabled()

    # Atomically CLAIM the token BEFORE any IdP side effect — this guarded consume is
    # the single-use serialization point. Two concurrent redemptions of the same token
    # race here and exactly one wins; the loser sees None → 400 and never reaches
    # set_authentik_password, so the password can never be set twice. (A non-consuming
    # check here would leave a window where both requests set the password — the P1 the
    # earlier ordering had.) On ANY failure below we RELEASE the claim so the holder can
    # retry, since the password was never actually set.
    email = await consume_activation_token(data.token)
    if email is None:
        raise HTTPException(400, "Invalid, expired, or already-used activation link")

    try:
        # Defensive: a token is only ever issued alongside an account, but confirm one
        # exists (and get a name for provisioning) before touching the IdP.
        name = await _lookup_account_name(email)
        if name is None:
            raise HTTPException(400, "Invalid, expired, or already-used activation link")

        # Provision-ensure (idempotent self-heal if account-creation provisioning
        # failed), then set the password. Provisioning is best-effort/never-raises;
        # the password set is must-succeed (log the email only, never the password).
        await provision_authentik_account(email, name)
        await set_authentik_password(email, data.password.get_secret_value())
    except HTTPException:
        await release_activation_token(data.token)  # keep the token live (e.g. no account)
        raise
    except Exception:
        await release_activation_token(data.token)  # IdP failure → retryable
        log.error("failed to set Authentik password for %s", email, exc_info=True)
        raise HTTPException(
            502, "Could not set your password. Please try again."
        ) from None

    return {"ok": True, "email": email}
