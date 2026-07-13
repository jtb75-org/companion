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
  * Identity resolved by EMAIL claim (Companion has no external_subject_id column
    yet — that column + sub-based resolution is the NEXT PR). The opaque Authentik
    `sub` is what gets stored in the session (privacy: no email/PII in Redis).
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
from app.auth.ratelimit import get_login_rate_limiter
from app.auth.session import get_session_store
from app.config import settings
from app.db import get_db
from app.db.context import set_login_email_context
from app.models.audit import AccountAuditLog
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

    email = (verified.email or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "identity provider token missing email")

    # Invite-only gate (mirrors app/api/v1/profile.complete_profile). Resolve the
    # user by EMAIL claim — exactly like the current Firebase get_current_user
    # path. TODO(next PR): resolve by `external_subject_id` (verified.sub) once that
    # column exists, instead of email-matching.
    # RLS bootstrap: set the login-email GUC so the users policy admits this lookup.
    await set_login_email_context(db, email)
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        # No row => this email was never invited. Refuse; do NOT auto-provision.
        # Audit on its own commit so it survives the 403's request rollback.
        db.add(AccountAuditLog(event="signup_refused", email=email))
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No invitation found for this account.")
    if user.account_status in _INACTIVE_STATUSES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")

    await limiter.reset(user_bucket)  # clear the throttle on a successful login
    # Store the opaque Authentik subject (not the email) — no PII in Redis.
    sid = await get_session_store().create(verified.sub)
    _set_cookie(response, settings.session_cookie_name, sid, http_only=True)
    _set_cookie(response, settings.csrf_cookie_name, secrets.token_urlsafe(32), http_only=False)
    return {"status": "ok"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    """Revoke the session and clear cookies."""
    _require_authentik_enabled()
    sid = request.cookies.get(settings.session_cookie_name)
    if sid:
        await get_session_store().delete(sid)
    _clear_cookie(response, settings.session_cookie_name)
    _clear_cookie(response, settings.csrf_cookie_name)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
