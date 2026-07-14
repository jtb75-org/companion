"""BFF native-login endpoints (companion-authentik, PR #2 — ADDITIVE AND INERT).

The SPA/app posts credentials here; we authenticate against Authentik server-side
(no redirect/hosted UI), enforce Companion's invite-only gate, and set an httpOnly
session cookie + a double-submit CSRF cookie.

CRITICAL: this whole surface is gated behind ``settings.authentik_enabled``
(DEFAULT False). While the master ``auth_provider`` is "firebase" these endpoints
return 404 and touch nothing — Firebase stays the live auth for every existing
endpoint, and no session minted here is consumed yet (the auth deps still read
only the Firebase bearer). The actual cutover — resolving requests from this
session and rewiring the ~10 verify_firebase_token call sites — is a later PR.

Companion adaptations vs the HealthCostClarity original:
  * Invite-only: HCC JIT-provisions on first login; Companion REFUSES an email
    with no pre-existing User row (mirrors app/api/v1/profile.complete_profile —
    only an `invited` stub or existing user may proceed). No member is auto-created.
  * Identity resolved by the stable ``external_subject_id`` (the Authentik
    per-provider ``sub``), falling back to the EMAIL claim on the first Authentik
    login of an existing member — at which point the sub is lazily backfilled onto
    the row so subsequent logins resolve by subject (PR #3). The opaque ``sub`` is
    what gets stored in the session (privacy: no email/PII in Redis).
"""

from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authentik_flow import (
    AuthenticationFailed,
    AuthentikFlowAuthenticator,
    FlowError,
    MfaRequired,
)
from app.auth.oidc import OIDCVerifier, TokenError
from app.auth.principal import _bearer_session_token
from app.auth.ratelimit import get_login_rate_limiter
from app.auth.session import get_session_store
from app.config import settings
from app.db import get_db
from app.db.context import (
    set_login_email_context,
    set_login_subject_context,
    set_user_context,
)
from app.db.session import maintenance_session
from app.models.audit import AccountAuditLog
from app.models.trusted_contact import TrustedContact
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("companion.auth")

_INACTIVE_STATUSES = ("deactivated", "pending_deletion")

_verifier: OIDCVerifier | None = None


def _require_authentik_enabled() -> None:
    """Gate: while auth_provider != 'authentik' this surface does not exist (404).

    Keeps the router harmless to include in main.py — flipping the flag is the
    only thing that activates it, and the existing Firebase paths are untouched."""
    if not settings.authentik_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def get_authentik_verifier() -> OIDCVerifier:
    """Lazily-built process-wide OIDC verifier (JWKS cached inside)."""
    global _verifier
    if _verifier is None:
        _verifier = OIDCVerifier(
            issuer=settings.authentik_oidc_issuer,
            jwks_uri=settings.authentik_oidc_jwks_uri,
            audience=settings.oidc_audience,
        )
    return _verifier


def _authenticator() -> AuthentikFlowAuthenticator:
    return AuthentikFlowAuthenticator(
        base_url=settings.authentik_internal_url,
        auth_flow_slug=settings.authentik_auth_flow_slug,
        client_id=settings.authentik_oidc_client_id,
        redirect_uri=settings.bff_oidc_redirect_uri,
    )


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=320)  # email or username
    password: str = Field(min_length=1, max_length=512)
    # Mobile (RN) clients set this true to receive the session token in the body
    # (they have no usable httpOnly cookie jar). Web clients omit it and rely
    # SOLELY on the httpOnly companion_sid cookie — so the sid is never exposed to
    # browser JS, preserving the full httpOnly/XSS posture (safety follow-up).
    mobile: bool = False


def _set_cookie(response: Response, name: str, value: str, *, http_only: bool) -> None:
    kwargs: dict = {
        "key": name,
        "value": value,
        "max_age": settings.session_ttl_seconds,
        "httponly": http_only,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if settings.session_cookie_domain:
        kwargs["domain"] = settings.session_cookie_domain
    response.set_cookie(**kwargs)


def _clear_cookie(response: Response, name: str) -> None:
    domain = settings.session_cookie_domain or None
    response.delete_cookie(key=name, path="/", domain=domain)


async def _mint_session(response: Response, *, subject: str, mobile: bool) -> dict:
    """Create a BFF session for ``subject`` and set the auth cookies.

    Stores only the opaque Authentik subject in Redis (no PII). Web clients get the
    session via the httpOnly cookie alone; a mobile client (``mobile`` true) also
    receives the opaque session id in the body to store in the Keychain and present as
    ``Authorization: Bearer``. Shared by the member and caregiver login paths so the
    cookie/CSRF/mobile-gating behavior is identical."""
    sid = await get_session_store().create(subject)
    csrf = secrets.token_urlsafe(32)
    _set_cookie(response, settings.session_cookie_name, sid, http_only=True)
    _set_cookie(response, settings.csrf_cookie_name, csrf, http_only=False)
    result: dict = {"status": "ok"}
    if mobile:
        result["session_token"] = sid
        result["csrf_token"] = csrf
    return result


async def _try_caregiver_login(
    email: str, subject: str, response: Response, *, mobile: bool
) -> dict | None:
    """Mint a caregiver BFF session if ``email`` is an active trusted contact, else None.

    Caregivers have no ``users`` row: they authenticate as the PERSON (the opaque
    subject) and act for a specific member via the explicit ``user_id`` gate at request
    time (``caregiver_authorized_for_member``). Here we only admit the person — the
    verified email must match an ACTIVE ``trusted_contacts`` row — and lazy-backfill the
    subject on ALL their active rows so subsequent requests resolve by subject
    (``resolve_caregiver_session``). Returns the login response dict, or ``None`` when
    the email matches no active caregiver row (the caller then refuses, exactly as
    before for a non-member email).

    Runs on the MAINTENANCE (BYPASSRLS) session because ``trusted_contacts`` is under
    per-member RLS (030) and no member GUC exists at login. ``email`` is IdP-verified
    (email_verified was already asserted above), so binding it is safe."""
    async with maintenance_session() as mdb:
        rows = (
            await mdb.execute(
                select(TrustedContact).where(
                    TrustedContact.contact_email == email,
                    TrustedContact.is_active.is_(True),
                )
            )
        ).scalars().all()
        if not rows:
            return None
        for contact in rows:
            if contact.external_subject_id is None:
                contact.external_subject_id = subject
            elif contact.external_subject_id != subject:
                # This verified email is already bound to a DIFFERENT subject — a
                # caregiver's stable identity would be changing under us. Refuse rather
                # than overwrite (mirrors the member subject-mismatch guard); the raise
                # rolls back the whole maintenance transaction, so no partial backfill.
                log.warning(
                    "BFF caregiver login subject mismatch for contact %s: "
                    "token sub != stored subject",
                    contact.id,
                )
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "Account identity mismatch"
                )
        await mdb.commit()
    return await _mint_session(response, subject=subject, mobile=mobile)


def _client_ip(request: Request) -> str:
    # Behind Cloudflare + traefik; prefer the edge-provided client IP.
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Authenticate via Authentik, enforce invite-only, and start a session."""
    _require_authentik_enabled()

    limiter = get_login_rate_limiter()
    user_bucket = f"user:{body.username.strip().lower()}"
    ip_bucket = f"ip:{_client_ip(request)}"
    # Increment both buckets; refuse if either is over the threshold (brute-force /
    # credential-stuffing throttle). A successful login clears the username bucket.
    over = max(await limiter.hit(user_bucket), await limiter.hit(ip_bucket))
    if over > settings.login_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many login attempts — try again later",
            headers={"Retry-After": str(settings.login_window_seconds)},
        )

    try:
        tokens = await _authenticator().authenticate(body.username, body.password)
    except AuthenticationFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from exc
    except MfaRequired as exc:
        # Phase A doesn't drive MFA/passkey stages yet (Phase B).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA is not supported yet") from exc
    except (FlowError, httpx.HTTPError) as exc:
        log.warning("BFF login flow error: %r", exc)  # surfaces real backend faults
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "authentication backend error") from exc

    try:
        # require_issuer=False: this id_token was obtained by the BFF directly from
        # Authentik's token endpoint over the in-cluster channel, so Authentik
        # stamped iss with the internal host (issuer_mode=per_provider). Signature
        # + audience verification still prove provenance; see OIDCVerifier.verify.
        verified = get_authentik_verifier().verify(tokens.id_token, require_issuer=False)
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "invalid token from identity provider"
        ) from exc

    # Safety gate (safety-reviewer follow-up #4, PR #71): the subject is the stable
    # identity we look up, backfill, and mint the session on — an empty/absent sub
    # would collapse those to a NULL match. Refuse before it is ever used.
    sub = (verified.sub or "").strip()
    if not sub:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "identity provider token missing subject"
        )

    email = (verified.email or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "identity provider token missing email")

    # Cutover gate #5 (safety-reviewer): the invite-only resolution and lazy backfill
    # bind a member row to this email. Only trust the email claim if the IdP asserts
    # it is verified — otherwise an account with an attacker-chosen unverified email
    # could be pointed at another member's invite. Refuse an unverified email outright.
    if not verified.email_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "email address is not verified with the identity provider"
        )

    # Invite-only gate (mirrors app/api/v1/profile.complete_profile). Resolve the
    # member by the stable OIDC subject first (external_subject_id == sub); this is
    # the steady-state path once a member has logged in via Authentik at least once.
    # RLS bootstrap: set the login-subject GUC so the users policy (036) admits this
    # by-subject read (read-only bootstrap; writes stay fenced to the tenant GUC).
    await set_login_subject_context(db, sub)
    user = (
        await db.execute(select(User).where(User.external_subject_id == sub))
    ).scalar_one_or_none()

    if user is None:
        # No subject match → this is either the member's first Authentik login (sub
        # not yet stored) or an uninvited email. Fall back to the by-email lookup,
        # exactly like the current Firebase get_current_user path.
        # RLS bootstrap: set the login-email GUC so the users policy admits this lookup.
        await set_login_email_context(db, email)
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            # Not a member. Before refusing, admit an invited/active CAREGIVER — a
            # verified email matching an active trusted_contacts row. Returns a session
            # response, or None if this email is no caregiver either.
            caregiver_login = await _try_caregiver_login(
                email, sub, response, mobile=body.mobile
            )
            if caregiver_login is not None:
                await limiter.reset(user_bucket)  # clear the throttle on success
                return caregiver_login
            # No member AND no caregiver => this email was never invited. Refuse; do NOT
            # auto-provision. Audit on its own commit so it survives the 403's
            # request rollback.
            db.add(AccountAuditLog(event="signup_refused", email=email))
            await db.commit()
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "No invitation found for this account."
            )

    if user.account_status in _INACTIVE_STATUSES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")

    # Lazy backfill: bind this member's row to the stable subject on first Authentik
    # login so every subsequent login resolves by sub (above) and never touches the
    # email path. If a DIFFERENT subject is already bound, a member's stable identity
    # would be changing under us — treat as an anomaly and refuse rather than
    # overwrite (prevents an attacker-controlled sub from hijacking a member row).
    if user.external_subject_id is None:
        # Bootstrap the tenant GUC to the now-authenticated member's id so the
        # backfill UPDATE satisfies the users RLS WITH CHECK (id = current_user_id);
        # the login-email GUC only admits READS. Without this the write would be
        # RLS-refused under the NOBYPASSRLS app role once Authentik is live.
        await set_user_context(db, user.id)
        user.external_subject_id = sub
        await db.commit()
    elif user.external_subject_id != sub:
        log.warning(
            "BFF login subject mismatch for user %s: token sub != stored subject",
            user.id,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account identity mismatch")

    await limiter.reset(user_bucket)  # clear the throttle on a successful login
    # Store the opaque Authentik subject (not the email) — no PII in Redis. This is
    # the same stable subject now persisted as external_subject_id above. Web clients
    # get the session via the httpOnly cookie alone; a mobile client (body.mobile)
    # also receives the opaque sid in the body (Keychain → Authorization: Bearer,
    # non-ambient → no CSRF; see app/auth/principal.resolve_session_subject).
    return await _mint_session(response, subject=sub, mobile=body.mobile)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    """Revoke the session (server-side) and clear cookies.

    Web clients present the session via the ``companion_sid`` cookie; mobile
    clients present it as ``Authorization: Bearer <sid>`` and have no cookie jar.
    We must revoke BOTH so logout actually kills the Redis session — otherwise a
    copied/stolen bearer would survive logout until its TTL. A Firebase id_token
    passed as the bearer is not a session key, so its delete simply misses."""
    _require_authentik_enabled()
    store = get_session_store()
    cookie_sid = request.cookies.get(settings.session_cookie_name)
    bearer_sid = _bearer_session_token(request)
    for sid in {s for s in (cookie_sid, bearer_sid) if s}:
        await store.delete(sid)
    _clear_cookie(response, settings.session_cookie_name)
    _clear_cookie(response, settings.csrf_cookie_name)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
