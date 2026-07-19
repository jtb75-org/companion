"""Member self-signup (POST /auth/signup) + the activation INVITED->ACTIVE flip.

Self-signup opens self-registration (previously closed): an individual creates their
own INVITED, self_directed member account and gets a branded activation email; the
activation link is the email-ownership proof. This surface is UNAUTHENTICATED and
security-sensitive, so the tests pin the security envelope: inert when not
authentik (404),
anti-enumeration (byte-identical response regardless of account existence), the
per-IP rate limit, and that activation flips the member ACTIVE.

Authentik + Redis are mocked; a real Postgres session is used (mirrors
test_authentik_login.py / test_activation.py). ``auth_provider`` is set via monkeypatch.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, select

import app.auth.ratelimit as ratelimit_module
from app.auth.ratelimit import InMemoryRateLimiter
from app.config import settings
from app.db import session as db_module
from app.models.activation_token import ActivationToken
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus, CareModel
from app.models.user import User
from app.services import activation_service
from tests.conftest import requires_db

_SIGNUP = "/auth/signup"


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _delete_user(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.execute(delete(ActivationToken).where(ActivationToken.email == email))
        await s.commit()


async def _get_user(email: str) -> User | None:
    async with db_module.async_session_factory() as s:
        return (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()


async def _seed_user(email: str, *, status: AccountStatus, name: str = "Seed") -> None:
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name=name,
                display_name=name,
                account_status=status,
            )
        )
        await s.commit()


class _Spies:
    """Spies for the two side-effecting seams the signup endpoint drives.

    ``provision`` is patched on invitation_service (where get_or_create_stub_user calls
    it) so the REAL stub creation still runs; ``activation`` replaces the whole
    send-activation helper as imported into the auth_authentik module namespace."""

    def __init__(self, monkeypatch) -> None:
        self.provision: list[tuple[str, str]] = []
        self.activation: list[tuple[str, str]] = []

        async def _provision(email: str, name: str) -> None:
            self.provision.append((email, name))

        async def _activation(email: str, name: str) -> None:
            self.activation.append((email, name))

        monkeypatch.setattr(
            "app.services.invitation_service.provision_authentik_account", _provision
        )
        monkeypatch.setattr(
            "app.api.auth_authentik.send_activation_if_enabled", _activation
        )


def _enable_authentik(monkeypatch) -> InMemoryRateLimiter:
    """Flip auth_provider=authentik and swap in an in-memory rate limiter (no Redis)."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    limiter = InMemoryRateLimiter()
    monkeypatch.setattr(ratelimit_module, "_limiter", limiter)
    return limiter


# ── 1. inert when provider is not authentik: 404 + no side effects ──────────────


@requires_db
async def test_signup_404_when_not_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    assert settings.authentik_enabled is False  # sanity
    spies = _Spies(monkeypatch)
    email = f"su-fb-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        r = await ac.post(_SIGNUP, json={"email": email, "name": "Fb User"})
    assert r.status_code == 404
    # No account, no provisioning, no email.
    assert await _get_user(email) is None
    assert spies.provision == []
    assert spies.activation == []
    await _delete_user(email)


# ── 2. new email → INVITED, self_directed member + provision + activation ────────


@requires_db
async def test_signup_new_email_creates_invited_self_directed(monkeypatch):
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    email = f"su-new-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        r = await ac.post(_SIGNUP, json={"email": email, "name": "New Member"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    row = await _get_user(email)
    assert row is not None
    assert row.account_status == AccountStatus.INVITED
    assert row.care_model == CareModel.SELF_DIRECTED
    # Provisioned (via the real stub creation) + branded activation email fired.
    assert spies.provision == [(email, "New Member")]
    assert spies.activation == [(email, "New Member")]
    await _delete_user(email)


# ── 3. anti-enumeration: identical response; ACTIVE case does nothing ────────────


@requires_db
async def test_signup_anti_enumeration_identical_response(monkeypatch):
    """An existing ACTIVE email and a brand-new email return the BYTE-identical
    response, and the ACTIVE case creates no row + sends no email (no existence leak)."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    active_email = f"su-active-{uuid.uuid4()}@t.io"
    new_email = f"su-fresh-{uuid.uuid4()}@t.io"
    await _delete_user(active_email)
    await _delete_user(new_email)
    await _seed_user(active_email, status=AccountStatus.ACTIVE)

    async with _client() as ac:
        r_active = await ac.post(
            _SIGNUP, json={"email": active_email, "name": "Ann Active"}
        )
        r_new = await ac.post(_SIGNUP, json={"email": new_email, "name": "Ned New"})

    # Byte-identical response — no existence signal in status or body.
    assert r_active.status_code == r_new.status_code == 200
    assert r_active.content == r_new.content
    assert r_active.json() == {"status": "ok"}

    # ACTIVE case: no email sent, no provisioning, no duplicate/new row churn.
    assert (active_email, "Ann Active") not in spies.activation
    assert (active_email, "Ann Active") not in spies.provision
    assert spies.activation == [(new_email, "Ned New")]  # only the new email got mail
    active_row = await _get_user(active_email)
    assert active_row is not None and active_row.account_status == AccountStatus.ACTIVE
    await _delete_user(active_email)
    await _delete_user(new_email)


# ── 4. existing INVITED → re-fires activation, no duplicate row ──────────────────


@requires_db
async def test_signup_existing_invited_resends_no_duplicate(monkeypatch):
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    email = f"su-inv-{uuid.uuid4()}@t.io"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.INVITED, name="Ivy Invited")

    async with _client() as ac:
        r = await ac.post(_SIGNUP, json={"email": email, "name": "Ivy Invited"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    # Re-fired the activation email; the resend branch does NOT create a stub, so
    # provision is untouched and there is exactly one row.
    assert spies.activation == [(email, "Ivy Invited")]
    assert spies.provision == []
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(select(User).where(User.email == email))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].account_status == AccountStatus.INVITED
    await _delete_user(email)


# ── 5. per-IP rate limit → 429 with Retry-After ─────────────────────────────────


@requires_db
async def test_signup_rate_limited_by_ip(monkeypatch):
    """Exceeding signup_max_attempts from one IP returns 429 before any account work."""
    _enable_authentik(monkeypatch)
    _Spies(monkeypatch)
    monkeypatch.setattr(settings, "signup_max_attempts", 2)
    email = f"su-rl-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        r1 = await ac.post(_SIGNUP, json={"email": email, "name": "Rl One"})
        r2 = await ac.post(_SIGNUP, json={"email": email, "name": "Rl One"})
        r3 = await ac.post(_SIGNUP, json={"email": email, "name": "Rl One"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers.get("Retry-After") == str(settings.login_window_seconds)
    await _delete_user(email)


@requires_db
async def test_signup_per_email_cap_across_ips(monkeypatch):
    """Even from ROTATING IPs (so the per-IP limit never bites), activation mail for one
    address is capped at signup_email_max_per_window — bounding email-bombing a victim."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    monkeypatch.setattr(settings, "signup_email_max_per_window", 2)
    monkeypatch.setattr(settings, "signup_max_attempts", 100)  # keep per-IP out of the way
    email = f"su-cap-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        for i in range(5):
            r = await ac.post(
                _SIGNUP,
                json={"email": email, "name": "Cap"},
                headers={"cf-connecting-ip": f"9.9.9.{i}"},  # a fresh IP each time
            )
            assert r.status_code == 200  # response stays uniform even once capped
    # 1st = created (send), 2nd = resent (send), 3rd–5th = resent but over the cap (skip).
    assert len(spies.activation) == 2
    await _delete_user(email)


@requires_db
async def test_signup_rejects_markup_name(monkeypatch):
    """A name carrying HTML/markup is rejected at the boundary (422). Otherwise it would
    be interpolated into a brand-trusted activation email on this OPEN endpoint (the
    email-injection / phishing-amplification vector safety flagged)."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    async with _client() as ac:
        r = await ac.post(
            _SIGNUP,
            json={"email": "mk@t.io", "name": '<a href="https://evil">click</a>'},
        )
    assert r.status_code == 422
    assert spies.provision == []
    assert spies.activation == []


@requires_db
async def test_signup_concurrent_same_email(monkeypatch):
    """Concurrent signups for the same NEW email all return 200 and yield exactly ONE
    users row — get_or_create_stub_user is race-safe against the unique(email) constraint,
    so a lost insert re-selects instead of surfacing a 500 (which would both break the
    uniform anti-enumeration response and be a public DoS edge)."""
    _enable_authentik(monkeypatch)
    _Spies(monkeypatch)
    monkeypatch.setattr(settings, "signup_max_attempts", 100)  # keep per-IP out of the way
    email = f"su-race-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        results = await asyncio.gather(
            *[
                ac.post(
                    _SIGNUP,
                    json={"email": email, "name": "Race"},
                    headers={"cf-connecting-ip": f"7.7.7.{i}"},  # dodge the per-IP bucket
                )
                for i in range(4)
            ]
        )
    assert all(r.status_code == 200 for r in results)
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(select(User).where(User.email == email))
        ).scalars().all()
    assert len(rows) == 1  # exactly one, despite the concurrent creates
    await _delete_user(email)


# ── 6. invalid email → 422 (schema plausibility gate) ───────────────────────────


@requires_db
async def test_signup_rejects_implausible_email(monkeypatch):
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    async with _client() as ac:
        r = await ac.post(_SIGNUP, json={"email": "not-an-email", "name": "X"})
    assert r.status_code == 422
    assert spies.provision == []
    assert spies.activation == []


# ── 7. activation flips a self-signup member INVITED -> ACTIVE ───────────────────


@requires_db
async def test_activation_flips_member_invited_to_active(monkeypatch):
    """Seed an INVITED self_directed member, run the /activation/set-password happy path
    (IdP seams mocked), and assert the users row is now ACTIVE — the proof-of-email that
    completes a self-signup member."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    email = f"su-flip-{uuid.uuid4()}@t.io"
    password = "sunny-meadow-lake-42"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.INVITED, name="Mel Member")
    token = await activation_service.issue_activation_token(email)

    async def _provision(email_: str, name_: str) -> None:
        return None

    async def _set_password(email_: str, password_: str) -> None:
        return None

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr("app.api.v1.activation.set_authentik_password", _set_password)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": password},
        )
    assert r.status_code == 200, r.text

    row = await _get_user(email)
    assert row is not None
    assert row.account_status == AccountStatus.ACTIVE
    # Lifecycle traceability: the flip writes an account_activated audit event.
    async with db_module.async_session_factory() as s:
        events = (
            await s.execute(
                select(AccountAuditLog).where(
                    AccountAuditLog.email == email,
                    AccountAuditLog.event == "account_activated",
                )
            )
        ).scalars().all()
    assert len(events) == 1
    await _delete_user(email)
