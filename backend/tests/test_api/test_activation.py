"""Tests for the generic, email-keyed account-activation flow.

Covers the service (issue / resolve / consume single-use), the public /activation
endpoints (validate + branded set-password, INERT under firebase), and the admin-
creation wiring (activation email under Authentik only). A real Postgres session is
used (mirrors test_set_password_authentik.py); the Authentik admin HTTP seam is
monkeypatched as async spies. ``auth_provider`` is set via monkeypatch, not env.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.config import settings
from app.db import session as db_module
from app.models.activation_token import ActivationToken
from app.models.admin_user import AdminUser
from app.models.user import User
from app.services import activation_service
from tests.conftest import requires_db

# ── helpers ─────────────────────────────────────────────────────────────────────


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _delete_admin(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(AdminUser).where(AdminUser.email == email))
        await s.execute(delete(ActivationToken).where(ActivationToken.email == email))
        await s.commit()


async def _delete_user(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(ActivationToken).where(ActivationToken.email == email))
        await s.commit()


async def _seed_admin(email: str, name: str = "New Admin", role: str = "editor") -> None:
    async with db_module.async_session_factory() as s:
        s.add(AdminUser(email=email, name=name, role=role))
        await s.commit()


class _Spies:
    """Async spies for the Authentik admin seam on the activation endpoint module."""

    def __init__(self, monkeypatch) -> None:
        self.provision: list[tuple[str, str]] = []
        self.set_password: list[tuple[str, str]] = []

        async def _provision(email: str, name: str) -> None:
            self.provision.append((email, name))

        async def _set_password(email: str, password: str) -> None:
            self.set_password.append((email, password))

        monkeypatch.setattr(
            "app.api.v1.activation.provision_authentik_account", _provision
        )
        monkeypatch.setattr(
            "app.api.v1.activation.set_authentik_password", _set_password
        )


# ── 1. set-password 404 under firebase (inert) ──────────────────────────────────


@requires_db
async def test_set_password_404_under_firebase(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "firebase")
    assert settings.authentik_login_enabled is False  # sanity
    spies = _Spies(monkeypatch)
    email = f"act-fb-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email)
    token = await activation_service.issue_activation_token(email)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": "hunter2hunter"},
        )
    assert r.status_code == 404
    assert spies.provision == []
    assert spies.set_password == []
    await _delete_admin(email)


# ── 2. validate happy path + name; goes 404 after consume ───────────────────────


@requires_db
async def test_validate_returns_email_and_name_then_404_after_consume(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    email = f"act-val-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email, name="Ada Admin")
    token = await activation_service.issue_activation_token(email)

    async with _client() as ac:
        r = await ac.get("/api/v1/activation/validate", params={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "email": email, "name": "Ada Admin"}

        # Consume, then validate must 404 (single-use).
        consumed = await activation_service.consume_activation_token(token)
        assert consumed == email
        r2 = await ac.get("/api/v1/activation/validate", params={"token": token})
        assert r2.status_code == 404
    await _delete_admin(email)


# ── 3. validate 404 for expired + for a superseded (re-issued) token ────────────


@requires_db
async def test_validate_404_on_expired_and_superseded(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    email = f"act-exp-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email)

    # Expired token: issue then force expires_at into the past.
    expired = await activation_service.issue_activation_token(email)
    async with db_module.async_session_factory() as s:
        await s.execute(
            update(ActivationToken)
            .where(ActivationToken.token == expired)
            .values(expires_at=datetime.utcnow() - timedelta(hours=1))
        )
        await s.commit()

    # Superseded: the first token is invalidated by a re-issue.
    first = await activation_service.issue_activation_token(email)
    second = await activation_service.issue_activation_token(email)
    assert first != second

    async with _client() as ac:
        r_exp = await ac.get("/api/v1/activation/validate", params={"token": expired})
        assert r_exp.status_code == 404
        r_first = await ac.get("/api/v1/activation/validate", params={"token": first})
        assert r_first.status_code == 404  # superseded
        r_second = await ac.get(
            "/api/v1/activation/validate", params={"token": second}
        )
        assert r_second.status_code == 200  # newest still valid
    await _delete_admin(email)


# ── 4. set-password happy path (authentik): both seam calls + consume ───────────


@requires_db
async def test_set_password_happy_path(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    email = f"act-ok-{uuid.uuid4()}@t.io"
    password = "brand-new-pw-123"
    await _delete_admin(email)
    await _seed_admin(email, name="Owen Owner")
    token = await activation_service.issue_activation_token(email)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": password},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": email}
    assert spies.provision == [(email, "Owen Owner")]
    assert spies.set_password == [(email, password)]
    # Token consumed on success — a second set-password is refused.
    assert await activation_service.resolve_activation_email(token) is None
    async with _client() as ac:
        r2 = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": password},
        )
    assert r2.status_code == 400
    await _delete_admin(email)


# ── 5. set-password 400 on invalid / expired / used token ───────────────────────


@requires_db
async def test_set_password_400_on_invalid_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": f"nope-{uuid.uuid4().hex}", "password": "whatever12"},
        )
    assert r.status_code == 400
    assert spies.provision == []
    assert spies.set_password == []


@requires_db
async def test_set_password_400_on_used_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    spies = _Spies(monkeypatch)
    email = f"act-used-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email)
    token = await activation_service.issue_activation_token(email)
    # Consume it out from under the endpoint.
    assert await activation_service.consume_activation_token(token) == email

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": "whatever12"},
        )
    assert r.status_code == 400
    assert spies.provision == []
    assert spies.set_password == []
    await _delete_admin(email)


# ── 6. consume_activation_token is single-use ───────────────────────────────────


@requires_db
async def test_consume_is_single_use(monkeypatch):
    email = f"act-consume-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email)
    token = await activation_service.issue_activation_token(email)

    assert await activation_service.consume_activation_token(token) == email
    # Second consume finds no unused row.
    assert await activation_service.consume_activation_token(token) is None
    await _delete_admin(email)


# ── 7. create_admin_user calls the shared activation helper (+ provision) ────────


@requires_db
async def test_create_admin_calls_activation_helper(monkeypatch):
    """create_admin_user provisions then calls the shared send_activation_if_enabled
    helper with the new admin's email + name. (The helper's own inert/active behavior
    is covered in test 8.)"""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    from app.api.admin import admin_users as admin_mod
    from app.main import app

    helper_calls: list[tuple[str, str]] = []
    provisioned: list[tuple[str, str]] = []

    async def _helper(email_: str, name_: str) -> None:
        helper_calls.append((email_, name_))

    async def _provision(email_: str, name_: str) -> None:
        provisioned.append((email_, name_))

    monkeypatch.setattr(admin_mod, "send_activation_if_enabled", _helper)
    monkeypatch.setattr(admin_mod, "provision_authentik_account", _provision)

    email = f"act-create-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    app.dependency_overrides[admin_mod._admin] = lambda: SimpleNamespace(
        role="admin", email="tester@t.io"
    )
    try:
        async with _client() as ac:
            r = await ac.post(
                "/admin/admin-users",
                json={"email": email, "name": "Created Admin", "role": "editor"},
            )
        assert r.status_code == 201, r.text
    finally:
        app.dependency_overrides.pop(admin_mod._admin, None)

    assert provisioned == [(email, "Created Admin")]
    assert helper_calls == [(email, "Created Admin")]
    await _delete_admin(email)


# ── 8. send_activation_if_enabled: inert under firebase, issues+sends on authentik ─


@requires_db
async def test_send_activation_if_enabled_inert_under_firebase(monkeypatch):
    """The shared helper is a no-op on the Firebase default: no email, no token row."""
    monkeypatch.setattr(settings, "auth_provider", "firebase")
    sent: list = []

    async def _send(to_email: str, to_name: str, token: str) -> bool:
        sent.append((to_email, to_name, token))
        return True

    monkeypatch.setattr("app.integrations.email_service.send_activation_email", _send)
    email = f"act-help-fb-{uuid.uuid4()}@t.io"
    await _delete_admin(email)

    await activation_service.send_activation_if_enabled(email, "No One")

    assert sent == []
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(
                select(ActivationToken).where(ActivationToken.email == email)
            )
        ).scalars().all()
    assert rows == []
    await _delete_admin(email)


@requires_db
async def test_send_activation_if_enabled_issues_and_sends_under_authentik(monkeypatch):
    """Under Authentik the helper issues a real token and sends the branded email."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    sent: list = []

    async def _send(to_email: str, to_name: str, token: str) -> bool:
        sent.append((to_email, to_name, token))
        return True

    monkeypatch.setattr("app.integrations.email_service.send_activation_email", _send)
    email = f"act-help-ak-{uuid.uuid4()}@t.io"
    await _delete_admin(email)

    await activation_service.send_activation_if_enabled(email, "Ada Admin")

    assert len(sent) == 1
    to_email, to_name, token = sent[0]
    assert (to_email, to_name) == (email, "Ada Admin")
    assert await activation_service.resolve_activation_email(token) == email
    await _delete_admin(email)


# ── 9. single-use is enforced BEFORE the IdP side effect (P1 race fix) ───────────


@requires_db
async def test_set_password_claims_token_before_idp_call(monkeypatch):
    """The token is CLAIMED (consumed) BEFORE set_authentik_password runs, so a
    concurrent redemption of the same token can't also reach the IdP and set a second
    password. Proven deterministically: the set-password spy asserts the token no
    longer resolves by the time the IdP side effect runs."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    email = f"act-claim-{uuid.uuid4()}@t.io"
    await _delete_admin(email)
    await _seed_admin(email)
    token = await activation_service.issue_activation_token(email)

    seen: dict[str, str | None] = {}

    async def _provision(email_: str, name_: str) -> None:
        return None

    async def _set_password(email_: str, password_: str) -> None:
        # By the time the IdP side effect runs, the token must already be claimed.
        seen["resolved"] = await activation_service.resolve_activation_email(token)

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr("app.api.v1.activation.set_authentik_password", _set_password)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": "brand-new-pw-123"},
        )
    assert r.status_code == 200, r.text
    assert seen["resolved"] is None  # already claimed before the IdP call
    await _delete_admin(email)


# ── 10. an IdP failure RELEASES the token so the holder can retry ────────────────


@requires_db
async def test_set_password_releases_token_on_idp_failure(monkeypatch):
    """A must-succeed set failure returns 502 AND releases the claim (the password was
    never set), so a follow-up attempt with a working IdP succeeds on the same token."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    email = f"act-rel-{uuid.uuid4()}@t.io"
    password = "brand-new-pw-123"
    await _delete_admin(email)
    await _seed_admin(email)
    token = await activation_service.issue_activation_token(email)

    calls = {"n": 0}

    async def _provision(email_: str, name_: str) -> None:
        return None

    async def _set_password_flaky(email_: str, password_: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("authentik unreachable")
        # second attempt succeeds (no raise)

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr(
        "app.api.v1.activation.set_authentik_password", _set_password_flaky
    )

    async with _client() as ac:
        r1 = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": password},
        )
        assert r1.status_code == 502
        # Released → still valid for a retry.
        assert await activation_service.resolve_activation_email(token) == email
        r2 = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": password},
        )
        assert r2.status_code == 200, r2.text
    assert calls["n"] == 2
    await _delete_admin(email)
