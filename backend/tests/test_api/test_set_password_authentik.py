"""Branded set-password activation (PR 2 of the Authentik provisioning effort).

A first-time caregiver invitee sets their Authentik password through Companion's
branded UI (``POST /api/v1/invitations/set-password``) so they can then log in and
accept. The whole surface is INERT when ``auth_provider`` is not authentik (404).

The key signal is the invitee's ``users`` stub ``account_status``:
  * INVITED ⇒ never set a password ⇒ set-password proceeds; ``needs_password_setup``.
  * ACTIVE  ⇒ returning/established account ⇒ set-password is REFUSED (409) so a
    leaked/reused invite token can't reset an established account's credentials.

The Authentik admin HTTP calls (``provision_authentik_account`` +
``set_authentik_password``) are monkeypatched as async spies; a real Postgres session
is used (mirrors test_invite_accept_authentik.py). ``authentik_login_enabled`` is
derived from ``auth_provider`` — set explicitly via monkeypatch rather than env.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import settings
from app.db import session as db_module
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


async def _delete_user(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.commit()


async def _seed_member_with_pending_invitee(
    member_email: str,
    cg_email: str,
    *,
    token: str | None = None,
    stub_status: AccountStatus = AccountStatus.INVITED,
) -> uuid.UUID:
    """Create an active member + a PENDING TrustedContact for cg_email + a caregiver
    stub user (``stub_status`` INVITED by default; ACTIVE simulates an already-activated
    account). Mirrors ``create_member_invitation``. Returns the member id."""
    async with db_module.async_session_factory() as s:
        await s.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == cg_email)
        )
        await s.commit()
    async with db_module.async_session_factory() as s:
        member = User(
            email=member_email,
            preferred_name="M",
            display_name="M",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(member)
        s.add(
            User(
                email=cg_email,
                preferred_name="C",
                display_name="C",
                account_status=stub_status,
            )
        )
        await s.flush()
        s.add(
            TrustedContact(
                user_id=member.id,
                contact_name="Care Giver",
                contact_email=cg_email,
                relationship_type=RelationshipType.FAMILY,
                access_tier=AccessTier.TIER_2,
                is_active=False,
                invitation_status=InvitationStatus.PENDING,
                invitation_token=token,
            )
        )
        await s.commit()
        return member.id


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _Spies:
    """Async spies for the Authentik admin seam, installed on the endpoint module."""

    def __init__(self, monkeypatch) -> None:
        self.provision: list[tuple[str, str]] = []
        self.set_password: list[tuple[str, str]] = []

        async def _provision(email: str, name: str) -> None:
            self.provision.append((email, name))

        async def _set_password(email: str, password: str) -> None:
            self.set_password.append((email, password))

        monkeypatch.setattr(
            "app.api.v1.invitations.provision_authentik_account", _provision
        )
        monkeypatch.setattr(
            "app.api.v1.invitations.set_authentik_password", _set_password
        )


# ── 1. set-password 404 when provider is not authentik (inert) ──────────────────


@requires_db
async def test_set_password_404_when_not_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    assert settings.authentik_login_enabled is False  # sanity
    spies = _Spies(monkeypatch)
    member_email = f"sp-fb-member-{uuid.uuid4()}@t.io"
    cg_email = f"sp-fb-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/set-password",
            json={"token": token, "password": "hunter2hunter"},
        )
    assert r.status_code == 404
    assert spies.provision == []
    assert spies.set_password == []
    await _delete_user(member_email)
    await _delete_user(cg_email)


# ── 2. first-time success: INVITED stub + valid token ───────────────────────────


@requires_db
async def test_set_password_first_time_success(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    member_email = f"sp-ok-member-{uuid.uuid4()}@t.io"
    cg_email = f"sp-ok-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    password = "brand-new-pw-123"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/set-password",
            json={"token": token, "password": password},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": cg_email}
    # Both admin seam calls fired with the invitee's contact email; password passed through.
    assert spies.provision == [(cg_email, "Care Giver")]
    assert spies.set_password == [(cg_email, password)]
    await _delete_user(member_email)
    await _delete_user(cg_email)


# ── 3. 409 when the stub is already ACTIVE (established account) ─────────────────


@requires_db
async def test_set_password_409_when_stub_active(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    member_email = f"sp-act-member-{uuid.uuid4()}@t.io"
    cg_email = f"sp-act-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(
        member_email, cg_email, token=token, stub_status=AccountStatus.ACTIVE
    )

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/set-password",
            json={"token": token, "password": "should-not-apply"},
        )
    assert r.status_code == 409
    # No password was set on the established account.
    assert spies.provision == []
    assert spies.set_password == []
    await _delete_user(member_email)
    await _delete_user(cg_email)


# ── 3b. 422 on a weak password (policy gate), no IdP side effect ─────────────────


@requires_db
async def test_set_password_422_on_weak_password(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    member_email = f"sp-weak-member-{uuid.uuid4()}@t.io"
    cg_email = f"sp-weak-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    async with _client() as ac:
        # "password" is both too short (< 10) and a denylisted common password.
        r = await ac.post(
            "/api/v1/invitations/set-password",
            json={"token": token, "password": "password"},
        )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]  # non-empty plain policy message
    # The password is strength-gated BEFORE the IdP — nothing was provisioned/set.
    assert spies.provision == []
    assert spies.set_password == []
    await _delete_user(member_email)
    await _delete_user(cg_email)


# ── 4. 400 on an invalid/unknown token ──────────────────────────────────────────


@requires_db
async def test_set_password_400_on_invalid_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/set-password",
            json={"token": f"nonexistent-{uuid.uuid4().hex}", "password": "whatever12"},
        )
    assert r.status_code == 400
    assert spies.provision == []
    assert spies.set_password == []


# ── 5. validate: needs_password_setup + contact_email across modes ──────────────


@requires_db
async def test_validate_needs_password_setup_true_under_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    member_email = f"val-a-member-{uuid.uuid4()}@t.io"
    cg_email = f"val-a-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    async with _client() as ac:
        r = await ac.get("/api/v1/invitations/validate", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert body["contact_email"] == cg_email
    assert body["needs_password_setup"] is True
    await _delete_user(member_email)
    await _delete_user(cg_email)


@requires_db
async def test_validate_needs_password_setup_false_when_not_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    member_email = f"val-f-member-{uuid.uuid4()}@t.io"
    cg_email = f"val-f-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    async with _client() as ac:
        r = await ac.get("/api/v1/invitations/validate", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contact_email"] == cg_email
    assert body["needs_password_setup"] is False  # inert when not authentik
    await _delete_user(member_email)
    await _delete_user(cg_email)


@requires_db
async def test_validate_needs_password_setup_false_when_active(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    member_email = f"val-act-member-{uuid.uuid4()}@t.io"
    cg_email = f"val-act-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(
        member_email, cg_email, token=token, stub_status=AccountStatus.ACTIVE
    )

    async with _client() as ac:
        r = await ac.get("/api/v1/invitations/validate", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contact_email"] == cg_email
    # ACTIVE stub ⇒ returning user, not first-time ⇒ no branded set-password step.
    assert body["needs_password_setup"] is False
    await _delete_user(member_email)
    await _delete_user(cg_email)
