"""Generic, email-keyed account-activation routes (branded set-password).

An account is created (an admin now via ``admin_users``; members later via ``users``)
and an activation email carries a single-use token. The token holder validates the
link, sets their Authentik password in Companion's branded UI, then signs in via
``/auth/login``. This surface is generic on purpose — keyed by EMAIL, not a cohort id
— so admin and member activation share one backend.

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

from app.api.auth_authentik import _audit_login_event, _require_authentik_enabled
from app.auth.session import get_session_store
from app.db.session import maintenance_session
from app.integrations.authentik_admin import (
    provision_authentik_account,
    set_authentik_password,
)
from app.models.admin_user import AdminUser
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.schemas.activation import ActivationSetPassword
from app.services.activation_service import (
    consume_activation_token,
    release_activation_token,
    resolve_activation_email,
)
from app.services.password_policy import PasswordPolicyError, validate_password

log = logging.getLogger("companion.activation")

router = APIRouter(prefix="/activation", tags=["Activation"])


async def _lookup_account_name(email: str) -> str | None:
    """Return a display name for ``email`` across all three cohorts, else None.

    Resolves admin_users, then users, then an ACTIVE trusted_contacts (caregiver) row.
    This MUST agree exactly with /auth/forgot-password's ``_account_name_if_exists`` on
    who is eligible: reset tokens are ISSUED for any of the three cohorts, so redemption
    here must accept the same three — otherwise a caregiver clicks a valid reset link and
    hits a 400 (the dead-end niru/safety flagged). Caregivers use Authentik password auth
    (username/password → /auth/login), so a reset is meaningful for them.

    Runs on the maintenance (BYPASSRLS) session: this is pre-auth (no tenant GUC) and
    ``users``/``trusted_contacts`` are per-user RLS-fenced, so a normal session would
    fail-close. ``None`` means no account exists for the email at all."""
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
        contact = (
            await mdb.execute(
                select(TrustedContact).where(
                    TrustedContact.contact_email == email,
                    TrustedContact.is_active.is_(True),
                )
            )
        ).scalars().first()
        if contact is not None:
            return contact.contact_name or email
    return None


async def _subjects_for_email(email: str) -> set[str]:
    """Return every Authentik subject bound to ``email`` across all three cohorts.

    The BFF session stores only the opaque Authentik ``sub``, so revoking an account's
    sessions requires mapping its email back to that subject. Cohorts + style mirror
    /auth/forgot-password's ``_account_name_if_exists`` (member, then caregiver, then
    admin) — the same three that can be issued a reset token must all be revocable.

    We UNION every cohort rather than returning the first match. The same person is one
    Authentik account (one ``sub``), so this is normally a 0- or 1-element set; but the
    backfill is per-row and lazy, so an email can be a member whose ``users`` row has a
    NULL subject while its ACTIVE ``trusted_contacts`` row carries the real one. A
    first-match-wins lookup would read the NULL and silently skip the revoke — a revoke
    that quietly fails is the failure mode this gate exists to prevent.

    Runs on the maintenance (BYPASSRLS) session: this is pre-auth (no tenant GUC) and
    ``users``/``trusted_contacts`` are per-user RLS-fenced, so a normal session would
    fail-close. An empty set means no account here has ever logged in via Authentik —
    there is nothing to revoke."""
    subjects: set[str] = set()
    async with maintenance_session() as mdb:
        user = (
            await mdb.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is not None and user.external_subject_id:
            subjects.add(user.external_subject_id)
        contacts = (
            await mdb.execute(
                select(TrustedContact).where(
                    TrustedContact.contact_email == email,
                    TrustedContact.is_active.is_(True),
                )
            )
        ).scalars().all()
        subjects.update(c.external_subject_id for c in contacts if c.external_subject_id)
        admin = (
            await mdb.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if admin is not None and admin.external_subject_id:
            subjects.add(admin.external_subject_id)
    return subjects


async def _revoke_sessions_for_email(email: str) -> None:
    """Evict every live BFF session of ``email``'s account after its password changed.

    Pre-PHI gate #3. Without this a password reset only changes the Authentik credential:
    an already-stolen session cookie/bearer keeps working for the rest of its sliding TTL,
    so the user (or admin) resetting BECAUSE they suspect compromise does not actually
    evict the attacker — worst for admins, who are full-privilege.

    Called UNCONDITIONALLY, never gated on the reset marker: the ``reset=1`` in the link
    is client-supplied and untrusted, so no security decision may branch on it. A genuine
    first-time activation simply has no prior sessions, making this a harmless no-op.

    BEST-EFFORT: the password is already changed by the time we get here, so a Redis
    hiccup must not fail the request (that would strand the caller with a password they
    can't be sure of). Log loudly instead — this is a security control failing open on
    the eviction, not on the credential change."""
    try:
        subjects = await _subjects_for_email(email)
        if not subjects:
            return  # never logged in via Authentik → no subject → no sessions
        store = get_session_store()
        for subject in subjects:
            await store.revoke_all_for_subject(subject)
        log.info(
            "revoked BFF sessions for %s after password set (%d subject(s))",
            email,
            len(subjects),
        )
        # Traceability: a security-relevant eviction gets a durable audit row.
        # Best-effort in its own transaction (mirrors the login audits); ``details`` is
        # structured metadata only — never the opaque subject itself.
        await _audit_login_event(
            "sessions_revoked",
            email,
            details={"reason": "password_set", "subject_count": len(subjects)},
            best_effort=True,
        )
    except Exception:
        log.error(
            "failed to revoke sessions for %s after password set (best-effort) — "
            "existing sessions may survive the reset",
            email,
            exc_info=True,
        )
        # This is the branch that MATTERS for forensics: the password changed but the
        # eviction did not, so a stolen session survived a reset — the security control
        # failed OPEN. An app-log line alone is the wrong record for that; a control that
        # can fail open must fail loudly AND durably. Alert on this event.
        try:
            await _audit_login_event(
                "sessions_revoke_failed",
                email,
                details={"reason": "password_set"},
                best_effort=True,
            )
        except Exception:  # pragma: no cover — audit is itself best-effort
            log.error("failed to audit the session-revocation failure for %s", email)


async def _activate_member_if_invited(email: str) -> None:
    """Flip a member's ``users`` row INVITED -> ACTIVE once they've proven email
    ownership by redeeming the activation token AND setting a password.

    This is what makes a self-signup member fully active: they arrive INVITED (from
    /auth/signup or an invite), and only receiving the emailed activation link lets them
    set a password here — so this flip is gated on that proof. Best-effort / no-op
    otherwise: admins have no ``users`` row, already-ACTIVE members and deactivated/
    pending_deletion accounts are left untouched, and caregivers activate via the
    separate invitation-accept flow. Runs on the maintenance (BYPASSRLS) session — this
    is pre-auth (no tenant GUC) and ``users`` is per-user RLS-fenced. A failure here must
    NOT fail the request: the password is already set by the time we call this, so we log
    and continue (the member can still be activated on a later action)."""
    try:
        async with maintenance_session() as mdb:
            user = (
                await mdb.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is not None and user.account_status == AccountStatus.INVITED:
                user.account_status = AccountStatus.ACTIVE
                # Lifecycle traceability — same event the profile-completion
                # activation path records; written in this same transaction.
                mdb.add(
                    AccountAuditLog(
                        event="account_activated", email=email, user_id=user.id
                    )
                )
                await mdb.commit()
    except Exception:
        log.error(
            "failed to activate member %s after set-password (best-effort)",
            email,
            exc_info=True,
        )


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

    Authentik-only (404s if Authentik login is disabled). Does NOT mint a session or log
    the user in — the web calls ``/auth/login`` next. The token is consumed only AFTER a
    successful password set, so an IdP failure leaves it usable for a retry."""
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

        # Strength-gate BEFORE any IdP side effect. 422 (distinct from the 400
        # invalid/expired-token contract) carries the plain policy message. Raising an
        # HTTPException here falls into the ``except HTTPException`` below, which
        # RELEASES the claimed token — the password was never set, so the holder can
        # retry with a stronger one. The rejected password is never echoed/logged.
        try:
            validate_password(data.password.get_secret_value(), email=email)
        except PasswordPolicyError as e:
            raise HTTPException(422, e.message) from None

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

    # The credential changed → every session minted under the OLD one must die (pre-PHI
    # gate #3). Unconditional: a first-time activation has no sessions (no-op), and the
    # link's reset marker is untrusted client input we must not branch on. Best-effort.
    await _revoke_sessions_for_email(email)

    # Password is now set (email ownership proven via the redeemed token) — activate a
    # self-signup / invited member. Best-effort: it must not fail an already-succeeded
    # password set (see helper). No-op for admins (no users row) and already-active rows.
    await _activate_member_if_invited(email)

    return {"ok": True, "email": email}
