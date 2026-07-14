"""Service layer for generic, email-keyed account activation.

Backs the branded Authentik set-password flow (app/api/v1/activation.py). A token is
issued alongside an account (an admin now, members later) and lets that person set
their password in Companion's UI before their first login. Keyed by EMAIL, not by a
cohort id, so admins and members share one implementation.

Every read/write here runs on the maintenance (BYPASSRLS) / fallback session because a
token is redeemed BEFORE any authenticated tenant GUC exists, and activation_tokens is
deliberately not under per-user RLS (migration 040). Timezone-naive ``datetime.utcnow``
matches the codebase convention (see invitation_service).
"""

import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.db.session import maintenance_session
from app.models.activation_token import ActivationToken

log = logging.getLogger("companion.activation")


async def send_activation_if_enabled(email: str, name: str) -> None:
    """Under Authentik, issue an activation token + email the branded set-password link.

    Shared by every account-creation seam (admin + member). BEST-EFFORT: a token/mail
    failure must NEVER fail the already-committed account creation — log and continue
    (a re-issue on the next admin action recovers). NO-OP on the Firebase default
    (``authentik_login_enabled`` False), so those accounts keep Google sign-in and the
    default path stays byte-identical. Call it AFTER the account row is committed."""
    if not settings.authentik_login_enabled:
        return
    try:
        # Lazy import: email_service pulls integrations that need not load for the
        # (common) inert path, and keeps this service import-cycle-free.
        from app.integrations.email_service import send_activation_email

        token = await issue_activation_token(email)
        await send_activation_email(email, name, token)
    except Exception:
        log.error(
            "failed to issue/send activation email for %s", email, exc_info=True
        )


def generate_activation_token() -> str:
    """High-entropy, URL-safe activation token (mirrors generate_invitation_token)."""
    return secrets.token_urlsafe(36)


async def issue_activation_token(email: str, *, ttl_hours: int = 72) -> str:
    """Issue a fresh activation token for ``email`` and supersede any prior unused one.

    Supersede-by-marking (set ``used_at=now`` on the prior unused rows) rather than
    DELETE: it keeps a lightweight audit trail of superseded issuances and makes the
    single-use guard uniform (a superseded token is indistinguishable from a consumed
    one — both have ``used_at`` set), so a stale link a user still has silently stops
    working the moment a re-issue happens.

    Runs on the maintenance (BYPASSRLS) session — email-keyed and pre-auth.
    """
    now = datetime.utcnow()
    token = generate_activation_token()
    async with maintenance_session() as mdb:
        # Invalidate any still-unused token for this email so only the newest is live.
        await mdb.execute(
            update(ActivationToken)
            .where(
                ActivationToken.email == email,
                ActivationToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        mdb.add(
            ActivationToken(
                email=email,
                token=token,
                expires_at=now + timedelta(hours=ttl_hours),
            )
        )
        await mdb.commit()
    return token


async def resolve_activation_email(token: str) -> str | None:
    """Return the email for a still-valid token (unused + unexpired), else None.

    Does NOT consume the token — used by /activation/validate and as the pre-check in
    set-password (which consumes only AFTER a successful password set).
    """
    now = datetime.utcnow()
    async with maintenance_session() as mdb:
        row = (
            await mdb.execute(
                select(ActivationToken).where(ActivationToken.token == token)
            )
        ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    return row.email


async def release_activation_token(token: str) -> None:
    """Un-claim a token (``used_at`` → NULL) so a failed redemption can be retried.

    Called ONLY by the set-password endpoint after IT successfully claimed the token
    (via ``consume_activation_token``) but a later step failed before the password was
    set. It is safe to restore unconditionally: while this caller holds the claim, a
    concurrent request's guarded consume returned None (``used_at`` was set), so no one
    else could have claimed the same token — this row is unambiguously ours to release.
    An already-expired token stays dead (``resolve``/``consume`` still check expiry).
    """
    async with maintenance_session() as mdb:
        await mdb.execute(
            update(ActivationToken)
            .where(ActivationToken.token == token)
            .values(used_at=None)
        )
        await mdb.commit()


async def consume_activation_token(token: str) -> str | None:
    """Atomically mark a valid token used and return its email, else None.

    Single-use safe via a guarded ``UPDATE ... WHERE used_at IS NULL AND expires_at >
    now RETURNING email``: the WHERE clause is evaluated inside the same statement that
    flips ``used_at``, so two concurrent consumes race on the row and exactly one sees
    a match (the other's WHERE no longer holds → 0 rows → None).
    """
    now = datetime.utcnow()
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            update(ActivationToken)
            .where(
                ActivationToken.token == token,
                ActivationToken.used_at.is_(None),
                ActivationToken.expires_at > now,
            )
            .values(used_at=now)
            .returning(ActivationToken.email)
        )
        email = result.scalar_one_or_none()
        await mdb.commit()
    return email
