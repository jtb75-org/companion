"""complete-profile is invite-only: it resolves ONLY an existing member from the
Authentik BFF session, so it can never self-provision an account.

Invite-only enforcement + the signup_refused audit live at /auth/login (see
test_authentik_login.test_login_refuses_uninvited_email); a session only exists for an
already-invited member, so complete-profile is unreachable for an uninvited email.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import app.auth.session as session_module
from app.auth.session import InMemorySessionStore
from app.config import settings
from app.db import session as db_module
from app.main import app
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db

_ENDPOINT = "/api/v1/auth/complete-profile"


def _session_for(monkeypatch, subject: str) -> InMemorySessionStore:
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    return store


async def _delete_user(email: str):
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(
            delete(AccountAuditLog).where(AccountAuditLog.email == email)
        )
        await s.commit()


async def _audit_events(email: str) -> list[str]:
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(
                select(AccountAuditLog).where(AccountAuditLog.email == email)
            )
        ).scalars().all()
        return [r.event for r in rows]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_uninvited_subject_is_rejected(monkeypatch):
    """A session whose subject maps to no member row → 401; no account is created
    (invite-only: complete-profile never self-provisions)."""
    email = "uninvited-invite-test@example.com"
    await _delete_user(email)  # ensure no row exists
    store = _session_for(monkeypatch, "sub-uninvited")
    sid = await store.create("sub-uninvited")
    async with _client() as ac:
        r = await ac.post(
            _ENDPOINT,
            json={"first_name": "Mallory", "last_name": "Nope"},
            cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "t"},
            headers={"X-CSRF-Token": "t"},
        )
    assert r.status_code == 401
    # And no account was created.
    async with db_module.async_session_factory() as s:
        assert (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none() is None
    await _delete_user(email)


async def test_invited_stub_is_completed_and_activated(monkeypatch):
    email = "invited-invite-test@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        # The invited stub carries the subject bound at /auth/login.
        s.add(
            User(
                email=email,
                preferred_name="Inv",
                display_name="Inv",
                account_status=AccountStatus.INVITED,
                external_subject_id="sub-invited",
            )
        )
        await s.commit()
    store = _session_for(monkeypatch, "sub-invited")
    sid = await store.create("sub-invited")
    async with _client() as ac:
        r = await ac.post(
            _ENDPOINT,
            json={"first_name": "Jane", "last_name": "Doe"},
            cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "t"},
            headers={"X-CSRF-Token": "t"},
        )
    assert r.status_code == 200
    async with db_module.async_session_factory() as s:
        u = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert u.first_name == "Jane" and u.last_name == "Doe"
        assert u.account_status == AccountStatus.ACTIVE
    # The activation is audited.
    assert "account_activated" in await _audit_events(email)
    await _delete_user(email)
