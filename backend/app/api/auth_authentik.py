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
import re
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
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
from app.db.session import async_session_factory, maintenance_session
from app.integrations.email_service import APP_URL, send_password_reset_email
from app.models.admin_user import AdminUser
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.services.activation_service import (
    issue_activation_token,
    send_activation_if_enabled,
)
from app.services.invitation_service import get_or_create_stub_user

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("companion.auth")

_INACTIVE_STATUSES = ("deactivated", "pending_deletion")

# Light plausibility check only (no email-validator dep in the tree). The real
# email-ownership proof is the activation link — a self-registrant cannot get a
# password (and thus cannot log in) without receiving the email at that inbox — so
# this regex just rejects obvious garbage before we provision + send mail.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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
        # A private-CA bundle path (prod) or True/system CAs (dev). Only used when
        # authentik_internal_url is https; the flow authenticator is inert on firebase.
        verify=settings.authentik_ca_bundle_path or True,
    )


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=320)  # email or username
    password: str = Field(min_length=1, max_length=512)
    # Mobile (RN) clients set this true to receive the session token in the body
    # (they have no usable httpOnly cookie jar). Web clients omit it and rely
    # SOLELY on the httpOnly companion_sid cookie — so the sid is never exposed to
    # browser JS, preserving the full httpOnly/XSS posture (safety follow-up).
    mobile: bool = False


class SignupRequest(BaseModel):
    """Body for the unauthenticated POST /auth/signup (member self-signup)."""

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _plausible_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("enter a valid email address")
        return v

    @field_validator("name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        # Boundary hardening: this name is interpolated into a branded activation email
        # sent to a possibly-non-consenting address on an OPEN endpoint. Collapse all
        # whitespace (kills newline injection into the plaintext body) and reject markup
        # chars so a hostile name can't smuggle a link/HTML into either MIME part. The
        # email builder also HTML-escapes, so this is defense-in-depth.
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("name is required")
        if "<" in v or ">" in v:
            raise ValueError("name contains invalid characters")
        return v


class ForgotPasswordRequest(BaseModel):
    """Body for the unauthenticated POST /auth/forgot-password (self-service reset)."""

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _plausible_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("enter a valid email address")
        return v


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


async def _audit_login_event(
    event: str,
    email: str,
    *,
    user_id=None,
    details: dict | None = None,
    best_effort: bool = False,
) -> None:
    """Durably record a BFF auth event to account_audit_log in its OWN transaction.

    A dedicated session so the record survives a request/maintenance-session ROLLBACK —
    in particular a subject-mismatch/refusal 403, where we must keep the record even
    though the login transaction unwinds (same rationale as the signup_refused commit).
    ``account_audit_log`` is not RLS-fenced. No PII beyond the invited email; ``details``
    is structured metadata only (e.g. the role).

    ``best_effort`` (SUCCESS audits only): the session is already minted + delivered by
    the time we audit a success, so an audit-DB hiccup must NOT turn a valid login into a
    500 — log and continue. Refusal/mismatch audits keep best_effort=False so no login is
    refused without a durable record."""
    try:
        async with async_session_factory() as adb:
            adb.add(
                AccountAuditLog(
                    event=event, email=email, user_id=user_id, details=details
                )
            )
            await adb.commit()
    except Exception:
        if not best_effort:
            raise
        log.error(
            "failed to write %s login audit (best-effort, login still delivered)",
            event,
            exc_info=True,
        )


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
                await _audit_login_event(
                    "bff_login_subject_mismatch", email, details={"role": "caregiver"}
                )
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "Account identity mismatch"
                )
        await mdb.commit()
    return await _mint_session(response, subject=subject, mobile=mobile)


async def _try_admin_login(
    email: str, subject: str, response: Response, *, mobile: bool
) -> dict | None:
    """Mint an admin BFF session if ``email`` is an active admin, else ``None``.

    Admins are not members — a pure admin has no ``users`` row — so like caregivers they
    authenticate as the PERSON (subject). We admit the verified email if it matches an
    ACTIVE ``admin_users`` row and lazy-backfill the subject so subsequent requests
    resolve by subject (``resolve_admin_session``). Returns the login response dict, or
    ``None`` when the email is no admin (the caller then refuses).

    Runs on the MAINTENANCE (BYPASSRLS) session for symmetry with the caregiver path;
    ``admin_users`` is RLS-disabled so this is not strictly required. ``email`` is
    IdP-verified (email_verified was asserted above), so binding it is safe."""
    async with maintenance_session() as mdb:
        admin = (
            await mdb.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if admin is None:
            return None
        if not admin.is_active:
            # Parity with get_current_admin: an inactive admin is refused, not admitted.
            await _audit_login_event(
                "bff_login_refused",
                email,
                details={"role": "admin", "reason": "inactive_account"},
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Admin account is not active"
            )
        if admin.external_subject_id is None:
            admin.external_subject_id = subject
        elif admin.external_subject_id != subject:
            # Verified email already bound to a DIFFERENT subject — refuse rather than
            # overwrite (mirrors the member/caregiver subject-mismatch guard); the raise
            # rolls back the maintenance transaction, so no partial backfill.
            log.warning(
                "BFF admin login subject mismatch for admin %s: "
                "token sub != stored subject",
                admin.id,
            )
            await _audit_login_event(
                "bff_login_subject_mismatch", email, details={"role": "admin"}
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account identity mismatch")
        await mdb.commit()
    return await _mint_session(response, subject=subject, mobile=mobile)


def _client_ip(request: Request) -> str:
    """Best-effort client IP for the login rate-limit bucket (cutover gate #3).

    ``cf-connecting-ip`` is set by Cloudflare and — because the origin is reachable
    only through the cloudflared tunnel — cannot be spoofed by a client, so it is
    always trusted. The raw ``X-Forwarded-For`` chain CAN be client-injected unless a
    trusted proxy owns it, so it is consulted ONLY when ``trust_forwarded_for`` is
    enabled; otherwise we fall back to the direct peer. This keeps a spoofed XFF from
    evading or poisoning the brute-force throttle."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    if settings.trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return request.client.host if request.client else "unknown"


@router.post("/signup")
async def signup(
    body: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Member self-signup: open self-registration for the individual/family track.

    UNAUTHENTICATED and Authentik-only (404 under firebase). A self-registrant supplies
    an email + name; we create an INVITED, self_directed member stub, provision the
    matching Authentik account, and email the branded activation link. The activation
    link IS the email-ownership proof — the registrant cannot set a password (and thus
    cannot log in) without receiving the mail at that inbox — so no separate email
    verification is needed, and activation flips them INVITED -> ACTIVE.

    Security envelope:
      * IP rate limit (distinct ``signup:ip`` bucket, tighter ``signup_max_attempts``):
        the primary abuse control for unauthenticated account creation + outbound email.
      * Anti-enumeration: the response is byte-identical whether or not the email
        already exists. We NEVER reveal account existence — an existing ACTIVE email and
        a brand-new email both return ``200 {"status": "ok"}``.
    """
    _require_authentik_enabled()

    # Rate-limit by IP FIRST (the #1 abuse control here). Distinct bucket from login so a
    # user's own login attempts and sign-ups don't share a counter. Over threshold → 429.
    limiter = get_login_rate_limiter()
    ip_bucket = f"signup:ip:{_client_ip(request)}"
    if await limiter.hit(ip_bucket) > settings.signup_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many sign-up attempts — try again later",
            headers={"Retry-After": str(settings.login_window_seconds)},
        )

    email = body.email.strip().lower()
    name = body.name.strip()

    # Second bound, keyed on the EMAIL (not IP): cap how many activation mails an address
    # can receive per window, so an attacker rotating IPs still can't bomb one victim.
    # When over, we skip the SEND but keep the branch/response identical (anti-enumeration).
    async def _send_activation_capped() -> None:
        if await limiter.hit(f"signup:email:{email}") <= settings.signup_email_max_per_window:
            await send_activation_if_enabled(email, name)

    # Look up any existing row by email on the maintenance (BYPASSRLS) session — users is
    # per-user RLS-fenced and there is no tenant GUC pre-auth. We branch on status but the
    # HTTP response is identical in every branch (anti-enumeration).
    async with maintenance_session() as mdb:
        existing = (
            await mdb.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        existing_status = existing.account_status if existing else None

    if existing is None:
        # Brand-new: create an INVITED, self_directed member stub (care_model
        # server-defaults to self_directed, correct for a self-directed signup) +
        # provision the Authentik account, then send the branded activation email.
        await get_or_create_stub_user(db, email, name)
        await _send_activation_capped()
        result = "created"
    elif existing_status == AccountStatus.INVITED:
        # A not-yet-activated account (prior self-signup or an invited stub). Re-fire the
        # activation email so a stuck user can recover; issue_activation_token supersedes
        # any prior token. Create NO second row. Bounded against email-bombing by BOTH the
        # per-IP limit above and the per-email cap in _send_activation_capped.
        await _send_activation_capped()
        result = "resent"
    else:
        # Already ACTIVE (or deactivated/pending_deletion): they have a usable account —
        # do NOTHING and send no email. They should sign in / reset, not re-signup.
        result = "noop"

    # Best-effort audit in its own transaction (mirrors _audit_login_event): server-side
    # only, so an audit hiccup never fails the request and the response stays uniform.
    await _audit_login_event(
        "signup_requested", email, details={"result": result}, best_effort=True
    )
    return {"status": "ok"}


async def _account_name_if_exists(email: str) -> str | None:
    """Return a display name if ``email`` belongs to any account, else ``None``.

    Resolves across all three cohorts on the MAINTENANCE (BYPASSRLS) session — this is
    pre-auth (no tenant GUC) and ``users``/``trusted_contacts`` are per-user RLS-fenced,
    so a normal session would fail-close. Cohorts, in priority order:
      * member — any ``users`` row (INVITED/ACTIVE/deactivated all reset the same way;
        the reset link lands on the branded set-password page either way);
      * caregiver — an ACTIVE ``trusted_contacts`` row (mirrors _try_caregiver_login);
      * admin — an ACTIVE ``admin_users`` row (mirrors _try_admin_login).
    ``None`` means no account exists — the caller sends nothing but still returns 200."""
    async with maintenance_session() as mdb:
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
        admin = (
            await mdb.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if admin is not None and admin.is_active:
            return admin.name or email
    return None


async def _issue_and_send_reset(email: str, name: str) -> None:
    """Issue a fresh activation token and email the branded reset link. BEST-EFFORT.

    Reuses the activation token machinery (``issue_activation_token`` supersedes any
    prior unused token) and the SAME redemption endpoint (``/api/v1/activation/
    set-password``) — the link only carries a ``reset=1`` marker so the branded page can
    adjust its copy. A token or send failure must NOT change the uniform 200 response
    (anti-enumeration), so we log and continue.

    DEFERRED pre-PHI launch gates (tracked by coordinator, intentionally NOT done here):
      (a) Session invalidation on reset — revoke the account's existing Authentik
          sessions after the password is reset so a stolen live session can't outlive it.
      (b) Timing-equalize the send — dispatch this in the background so the
          account-exists path isn't measurably slower than the no-account path (a
          timing side-channel that partially undermines anti-enumeration; same latent
          issue as /auth/signup, to be fixed uniformly)."""
    try:
        token = await issue_activation_token(email)
        reset_url = f"{APP_URL}/activate?token={token}&reset=1"
        await send_password_reset_email(email, name, reset_url)
    except Exception:
        log.error(
            "failed to issue/send password-reset email for %s (best-effort)",
            email,
            exc_info=True,
        )


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
) -> dict[str, str]:
    """Self-service password reset: email a branded set-password link if an account exists.

    UNAUTHENTICATED and Authentik-only (404 under firebase). Mirrors /auth/signup's
    security envelope exactly:
      * IP rate limit (distinct ``reset:ip`` bucket, ``reset_max_attempts``): the primary
        abuse control for this unauthenticated account-probe + outbound-email surface.
      * Anti-enumeration: the response is byte-identical whether or not the email exists.
        We NEVER reveal account existence — an existing account and an unknown address
        both return ``200 {"status": "ok"}`` with no branching in the response.
      * Per-EMAIL cap (``reset:email``, ``reset_email_max_per_window``): bounds
        reset-mail bombing a single victim even from rotating IPs.

    When an account DOES exist (member / caregiver / admin), we issue an activation
    token and email a reset link that redeems through the EXISTING
    ``/api/v1/activation/set-password`` endpoint (unchanged) — that endpoint has no
    first-time-only guard, so it cleanly re-sets the password of an already-ACTIVE
    account. All side effects are best-effort and never alter the 200 response.
    """
    _require_authentik_enabled()

    # Rate-limit by IP FIRST (the #1 abuse control here). Distinct bucket from signup +
    # login so counters don't cross-contaminate. Over threshold → 429.
    limiter = get_login_rate_limiter()
    ip_bucket = f"reset:ip:{_client_ip(request)}"
    if await limiter.hit(ip_bucket) > settings.reset_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many password-reset attempts — try again later",
            headers={"Retry-After": str(settings.login_window_seconds)},
        )

    email = body.email.strip().lower()

    # Resolve existence + a display name on the maintenance (BYPASSRLS) session. We branch
    # on the result but the HTTP response is identical in every branch (anti-enumeration).
    name = await _account_name_if_exists(email)

    if name is not None:
        # Second, address-keyed bound: cap how many reset mails one address can receive
        # per window so an attacker rotating IPs still can't bomb one victim. When over,
        # skip the SEND but keep the branch/response identical (anti-enumeration).
        if await limiter.hit(f"reset:email:{email}") <= settings.reset_email_max_per_window:
            await _issue_and_send_reset(email, name)
            result = "sent"
        else:
            result = "capped"
    else:
        # No account — do nothing, send no email, still return 200 (no existence leak).
        result = "noop"

    # Best-effort audit in its own transaction (mirrors signup_requested): server-side
    # only, so an audit hiccup never fails the request and the response stays uniform.
    await _audit_login_event(
        "password_reset_requested", email, details={"result": result}, best_effort=True
    )
    return {"status": "ok"}


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
        # Role is unknown at this point (before member/caregiver/admin resolution).
        await _audit_login_event(
            "bff_login_refused", email, details={"reason": "email_unverified"}
        )
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
                await _audit_login_event(
                    "bff_login_success",
                    email,
                    details={"role": "caregiver"},
                    best_effort=True,
                )
                return caregiver_login
            # Not a member or caregiver. Admit an active ADMIN (admin_users email).
            admin_login = await _try_admin_login(
                email, sub, response, mobile=body.mobile
            )
            if admin_login is not None:
                await limiter.reset(user_bucket)  # clear the throttle on success
                await _audit_login_event(
                    "bff_login_success",
                    email,
                    details={"role": "admin"},
                    best_effort=True,
                )
                return admin_login
            # No member, caregiver, OR admin => never invited. Refuse; do NOT
            # auto-provision. Audit on its own commit so it survives the 403's
            # request rollback.
            db.add(AccountAuditLog(event="signup_refused", email=email))
            await db.commit()
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "No invitation found for this account."
            )

    if user.account_status in _INACTIVE_STATUSES:
        # Valid IdP creds against a DISABLED member account — a security-relevant signal.
        await _audit_login_event(
            "bff_login_refused",
            email,
            user_id=user.id,
            details={"role": "member", "reason": "inactive_account"},
        )
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
        await _audit_login_event(
            "bff_login_subject_mismatch", email, user_id=user.id, details={"role": "member"}
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account identity mismatch")

    await limiter.reset(user_bucket)  # clear the throttle on a successful login
    # Store the opaque Authentik subject (not the email) — no PII in Redis. This is
    # the same stable subject now persisted as external_subject_id above. Web clients
    # get the session via the httpOnly cookie alone; a mobile client (body.mobile)
    # also receives the opaque sid in the body (Keychain → Authorization: Bearer,
    # non-ambient → no CSRF; see app/auth/principal.resolve_session_subject).
    result = await _mint_session(response, subject=sub, mobile=body.mobile)
    # Audit AFTER the session is minted + delivered, best-effort (niru + safety): all
    # cohorts audit success post-mint for consistent "session delivered" semantics, and
    # a rare audit-DB error must not 500 an already-valid login.
    await _audit_login_event(
        "bff_login_success",
        email,
        user_id=user.id,
        details={"role": "member"},
        best_effort=True,
    )
    return result


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
