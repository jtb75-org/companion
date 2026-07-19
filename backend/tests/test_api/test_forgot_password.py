"""Self-service password reset (POST /auth/forgot-password) + reuse of the existing
activation/set-password redemption for an ACTIVE account.

/auth/forgot-password is UNAUTHENTICATED and security-sensitive, so the tests pin the
same envelope as signup: inert when not authentik (404), anti-enumeration (byte-identical
response whether or not the account exists), the per-IP rate limit, and that a real
activation token is issued + a reset email is attempted ONLY for an existing account.
The final test proves the reset link redeems through the UNCHANGED
/api/v1/activation/set-password to set the Authentik password of an ALREADY-ACTIVE
account (no first-time-only guard on that path).

Authentik + Redis are mocked; a real Postgres session is used (mirrors
test_signup.py / test_activation.py). ``auth_provider`` is set via monkeypatch.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

import app.auth.ratelimit as ratelimit_module
from app.auth.ratelimit import InMemoryRateLimiter
from app.config import settings
from app.db import session as db_module
from app.models.activation_token import ActivationToken
from app.models.admin_user import AdminUser
from app.models.audit import AccountAuditLog
from app.models.enums import (
    AccessTier,
    AccountStatus,
    InvitationStatus,
    RelationshipType,
)
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from tests.conftest import requires_db

_FORGOT = "/auth/forgot-password"


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _delete_user(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(AdminUser).where(AdminUser.email == email))
        await s.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == email)
        )
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.execute(delete(ActivationToken).where(ActivationToken.email == email))
        await s.commit()


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


async def _seed_admin(email: str, *, name: str = "Ada Admin") -> None:
    async with db_module.async_session_factory() as s:
        s.add(AdminUser(email=email, name=name, is_active=True))
        await s.commit()


async def _seed_caregiver(cg_email: str, *, name: str = "Cara Giver") -> str:
    """An ACTIVE trusted_contacts row (its member has no bearing on the reset lookup).

    Returns the member email so callers can clean it up. The caregiver has NO ``users``
    stub — a caregiver-only account authenticates as the person via trusted_contacts."""
    member_email = f"m-{uuid.uuid4()}@t.io"
    async with db_module.async_session_factory() as s:
        member = User(
            email=member_email,
            preferred_name="M",
            display_name="M",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(member)
        await s.flush()
        s.add(
            TrustedContact(
                user_id=member.id,
                contact_name=name,
                contact_email=cg_email,
                relationship_type=RelationshipType.FAMILY,
                access_tier=AccessTier.TIER_2,
                is_active=True,
                invitation_status=InvitationStatus.ACCEPTED,
            )
        )
        await s.commit()
    return member_email


async def _tokens_for(email: str) -> list[ActivationToken]:
    async with db_module.async_session_factory() as s:
        return (
            await s.execute(
                select(ActivationToken).where(ActivationToken.email == email)
            )
        ).scalars().all()


class _Spies:
    """Spy the reset-email send seam AS IMPORTED into the auth_authentik namespace.

    We replace ONLY the email send — the real issue_activation_token still runs, so the
    tests can assert a durable ActivationToken row was written and then redeem it."""

    def __init__(self, monkeypatch) -> None:
        self.reset: list[tuple[str, str, str]] = []  # (email, name, reset_url)

        async def _send(to_email: str, to_name: str, reset_url: str) -> bool:
            self.reset.append((to_email, to_name, reset_url))
            return True

        monkeypatch.setattr(
            "app.api.auth_authentik.send_password_reset_email", _send
        )


def _enable_authentik(monkeypatch) -> InMemoryRateLimiter:
    """Flip auth_provider=authentik and swap in an in-memory rate limiter (no Redis)."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    limiter = InMemoryRateLimiter()
    monkeypatch.setattr(ratelimit_module, "_limiter", limiter)
    return limiter


# ── 1. inert when provider is not authentik: 404 + no side effects ──────────────


@requires_db
async def test_forgot_404_when_not_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    assert settings.authentik_enabled is False  # sanity
    spies = _Spies(monkeypatch)
    email = f"fp-fb-{uuid.uuid4()}@t.io"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.ACTIVE)

    async with _client() as ac:
        r = await ac.post(_FORGOT, json={"email": email})
    assert r.status_code == 404
    assert spies.reset == []
    assert await _tokens_for(email) == []
    await _delete_user(email)


# ── 2. anti-enumeration: existing vs unknown → byte-identical 200 ────────────────


@requires_db
async def test_forgot_anti_enumeration_identical_response(monkeypatch):
    """An existing account and an unknown address return the BYTE-identical response;
    only the existing account gets a token + a reset email (no existence leak)."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    existing = f"fp-exist-{uuid.uuid4()}@t.io"
    unknown = f"fp-none-{uuid.uuid4()}@t.io"
    await _delete_user(existing)
    await _delete_user(unknown)
    await _seed_user(existing, status=AccountStatus.ACTIVE, name="Eve Existing")

    async with _client() as ac:
        r_exist = await ac.post(_FORGOT, json={"email": existing})
        r_none = await ac.post(_FORGOT, json={"email": unknown})

    # Byte-identical response — no existence signal in status or body.
    assert r_exist.status_code == r_none.status_code == 200
    assert r_exist.content == r_none.content
    assert r_exist.json() == {"status": "ok"}

    # Existing account: exactly one live token issued + one reset email attempted.
    tokens = await _tokens_for(existing)
    assert len(tokens) == 1
    assert spies.reset and spies.reset[0][0] == existing
    # The reset link redeems through /activate and carries the reset marker.
    assert "/activate?token=" in spies.reset[0][2]
    assert "reset=1" in spies.reset[0][2]

    # Unknown address: no token, no email.
    assert await _tokens_for(unknown) == []
    assert all(entry[0] != unknown for entry in spies.reset)
    await _delete_user(existing)
    await _delete_user(unknown)


# ── 3. caregiver + admin accounts also qualify ──────────────────────────────────


@requires_db
async def test_forgot_caregiver_and_admin_get_reset(monkeypatch):
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    cg_email = f"fp-cg-{uuid.uuid4()}@t.io"
    admin_email = f"fp-admin-{uuid.uuid4()}@t.io"
    await _delete_user(cg_email)
    await _delete_user(admin_email)
    await _seed_caregiver(cg_email)
    await _seed_admin(admin_email)

    async with _client() as ac:
        r_cg = await ac.post(_FORGOT, json={"email": cg_email})
        r_admin = await ac.post(_FORGOT, json={"email": admin_email})
    assert r_cg.status_code == r_admin.status_code == 200

    assert len(await _tokens_for(cg_email)) == 1
    assert len(await _tokens_for(admin_email)) == 1
    sent = {entry[0] for entry in spies.reset}
    assert cg_email in sent
    assert admin_email in sent
    await _delete_user(cg_email)
    await _delete_user(admin_email)


# ── 4. per-IP rate limit → 429 with Retry-After ─────────────────────────────────


@requires_db
async def test_forgot_rate_limited_by_ip(monkeypatch):
    _enable_authentik(monkeypatch)
    _Spies(monkeypatch)
    monkeypatch.setattr(settings, "reset_max_attempts", 2)
    email = f"fp-rl-{uuid.uuid4()}@t.io"
    await _delete_user(email)

    async with _client() as ac:
        r1 = await ac.post(_FORGOT, json={"email": email})
        r2 = await ac.post(_FORGOT, json={"email": email})
        r3 = await ac.post(_FORGOT, json={"email": email})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers.get("Retry-After") == str(settings.login_window_seconds)
    await _delete_user(email)


# ── 5. per-EMAIL cap across rotating IPs ─────────────────────────────────────────


@requires_db
async def test_forgot_per_email_cap_across_ips(monkeypatch):
    """Even from ROTATING IPs (per-IP limit never bites), reset mail for one address is
    capped at reset_email_max_per_window — bounding reset-mail bombing a victim."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    monkeypatch.setattr(settings, "reset_email_max_per_window", 2)
    monkeypatch.setattr(settings, "reset_max_attempts", 100)  # keep per-IP out of the way
    email = f"fp-cap-{uuid.uuid4()}@t.io"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.ACTIVE)

    async with _client() as ac:
        for i in range(5):
            r = await ac.post(
                _FORGOT,
                json={"email": email},
                headers={"cf-connecting-ip": f"8.8.8.{i}"},  # a fresh IP each time
            )
            assert r.status_code == 200  # response stays uniform even once capped
    assert len(spies.reset) == 2  # only the first two sends fire
    await _delete_user(email)


# ── 6. invalid email → 422 (schema plausibility gate) ───────────────────────────


@requires_db
async def test_forgot_rejects_implausible_email(monkeypatch):
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    async with _client() as ac:
        r = await ac.post(_FORGOT, json={"email": "not-an-email"})
    assert r.status_code == 422
    assert spies.reset == []


# ── 7. the reset token redeems through the UNCHANGED set-password for an ACTIVE acct ─


@requires_db
async def test_reset_token_sets_password_for_active_account(monkeypatch):
    """The whole point of the reset flow: an already-ACTIVE member requests a reset,
    receives a token, and redeems it through the EXISTING /api/v1/activation/set-password
    to set a NEW Authentik password. That endpoint has no first-time-only guard, so the
    ACTIVE status does not block the reset (unlike the invitations set-password path)."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    email = f"fp-active-{uuid.uuid4()}@t.io"
    new_password = "fresh-forest-river-88"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.ACTIVE, name="Al Active")

    # Step 1: request the reset — a real token lands in the DB, the send is spied.
    async with _client() as ac:
        r = await ac.post(_FORGOT, json={"email": email})
    assert r.status_code == 200
    tokens = await _tokens_for(email)
    assert len(tokens) == 1
    token = tokens[0].token

    # Step 2: redeem through the unchanged set-password endpoint (IdP seams mocked).
    set_pw_calls: list[tuple[str, str]] = []

    async def _provision(email_: str, name_: str) -> None:
        return None

    async def _set_password(email_: str, password_: str) -> None:
        set_pw_calls.append((email_, password_))

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr("app.api.v1.activation.set_authentik_password", _set_password)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": new_password},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": email}
    # The ACTIVE account's Authentik password was (re)set — reset succeeded.
    assert set_pw_calls == [(email, new_password)]

    # Account stays ACTIVE (the reset does not downgrade an established account).
    async with db_module.async_session_factory() as s:
        row = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
    assert row is not None and row.account_status == AccountStatus.ACTIVE
    # sanity: the send seam was exercised on the request leg
    assert spies.reset and spies.reset[0][0] == email
    await _delete_user(email)


# ── 8. an ACTIVE caregiver (no users stub) can redeem a reset token ──────────────


@requires_db
async def test_reset_token_sets_password_for_caregiver(monkeypatch):
    """niru/safety regression guard: a caregiver has an ACTIVE trusted_contacts row but
    NO users stub, yet /auth/forgot-password issues them a reset token — so the shared
    redemption endpoint MUST accept it. Previously _lookup_account_name resolved only
    admin_users + users, so the caregiver hit a 400 dead-end. Now it resolves the
    caregiver by contact_email and set-password proceeds; no member row is fabricated."""
    _enable_authentik(monkeypatch)
    spies = _Spies(monkeypatch)
    cg_email = f"fp-cg-redeem-{uuid.uuid4()}@t.io"
    new_password = "quiet-harbor-stone-73"
    await _delete_user(cg_email)
    member_email = await _seed_caregiver(cg_email, name="Cleo Caregiver")

    # Step 1: request the reset — a real token lands in the DB for the caregiver email.
    async with _client() as ac:
        r = await ac.post(_FORGOT, json={"email": cg_email})
    assert r.status_code == 200
    tokens = await _tokens_for(cg_email)
    assert len(tokens) == 1
    token = tokens[0].token
    assert spies.reset and spies.reset[0][0] == cg_email

    # Step 2: redeem through the unchanged set-password endpoint (IdP seams mocked).
    set_pw_calls: list[tuple[str, str]] = []

    async def _provision(email_: str, name_: str) -> None:
        return None

    async def _set_password(email_: str, password_: str) -> None:
        set_pw_calls.append((email_, password_))

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr("app.api.v1.activation.set_authentik_password", _set_password)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": new_password},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": cg_email}
    # The caregiver's Authentik password was set — no 400 dead-end.
    assert set_pw_calls == [(cg_email, new_password)]

    # No users row was fabricated/activated for the caregiver-only account.
    async with db_module.async_session_factory() as s:
        cg_user = (
            await s.execute(select(User).where(User.email == cg_email))
        ).scalar_one_or_none()
    assert cg_user is None
    await _delete_user(cg_email)
    await _delete_user(member_email)
