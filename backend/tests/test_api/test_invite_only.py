"""complete-profile is invite-only: no pre-existing User row => rejected."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import session as db_module
from app.main import app
from app.models.audit import AccountAuditLog
from app.models.enums import AccountStatus
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db

_ENDPOINT = "/api/v1/auth/complete-profile"


def _patch_token_email(monkeypatch, email: str):
    async def _fake_verify(token: str):
        return {"email": email}

    # complete_profile imports verify_firebase_token into its own module.
    monkeypatch.setattr("app.api.v1.profile.verify_firebase_token", _fake_verify)


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


async def test_uninvited_email_is_rejected(monkeypatch):
    email = "uninvited-invite-test@example.com"
    await _delete_user(email)  # ensure no row exists
    _patch_token_email(monkeypatch, email)
    async with _client() as ac:
        r = await ac.post(
            _ENDPOINT,
            json={"first_name": "Mallory", "last_name": "Nope"},
            headers={"Authorization": "Bearer dummy"},
        )
    assert r.status_code == 403
    # And no account was created.
    async with db_module.async_session_factory() as s:
        assert (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none() is None
    # The refused signup is audited.
    assert "signup_refused" in await _audit_events(email)
    await _delete_user(email)


async def test_invited_stub_is_completed_and_activated(monkeypatch):
    email = "invited-invite-test@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="Inv",
                display_name="Inv",
                account_status=AccountStatus.INVITED,
            )
        )
        await s.commit()
    _patch_token_email(monkeypatch, email)
    async with _client() as ac:
        r = await ac.post(
            _ENDPOINT,
            json={"first_name": "Jane", "last_name": "Doe"},
            headers={"Authorization": "Bearer dummy"},
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
