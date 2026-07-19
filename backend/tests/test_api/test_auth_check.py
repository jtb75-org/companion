"""auth/check: admins without a member row are not forced into onboarding."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.auth import principal as principal_module
from app.config import settings
from app.db import session as db_module
from app.main import app
from app.models.admin_user import AdminUser
from app.models.enums import AccountStatus
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db

_ENDPOINT = "/api/v1/auth/check"
_SUBJECT = "test-subject-auth-check"


def _patch_session(monkeypatch, subject: str):
    """Pretend the request carries a valid BFF session for ``subject`` (Authentik)."""
    monkeypatch.setattr(settings, "dev_auth_bypass", False)

    async def _fake_subject(_request):
        return subject

    monkeypatch.setattr(principal_module, "resolve_session_subject", _fake_subject)


async def _cleanup(email: str):
    async with db_module.async_session_factory() as s:
        await s.execute(delete(AdminUser).where(AdminUser.email == email))
        await s.execute(delete(User).where(User.email == email))
        await s.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _check(monkeypatch, subject):
    _patch_session(monkeypatch, subject)
    async with _client() as ac:
        return await ac.get(_ENDPOINT)


async def test_admin_without_member_row_is_profile_complete(monkeypatch):
    email = "admin-authcheck-test@example.com"
    await _cleanup(email)
    async with db_module.async_session_factory() as s:
        s.add(
            AdminUser(
                email=email,
                name="Admin T",
                role="admin",
                is_active=True,
                external_subject_id=_SUBJECT,
            )
        )
        await s.commit()
    r = await _check(monkeypatch, _SUBJECT)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin"
    assert body["authorized"] is True
    # No member row -> not forced into member onboarding.
    assert body["profile_complete"] is True
    assert body["has_account"] is False
    await _cleanup(email)


async def test_member_with_incomplete_profile_is_not_complete(monkeypatch):
    email = "member-authcheck-test@example.com"
    await _cleanup(email)
    async with db_module.async_session_factory() as s:
        # Invited stub member: row exists, names missing. The subject binds to the
        # users row so _email_for_subject recovers the email from it.
        s.add(
            User(
                email=email,
                preferred_name="M",
                display_name="M",
                account_status=AccountStatus.INVITED,
                external_subject_id=_SUBJECT,
            )
        )
        # Also an admin row so authorize_by_email admits the caller (auth is a
        # separate concern here).
        s.add(AdminUser(email=email, name="M", role="viewer", is_active=True))
        await s.commit()
    r = await _check(monkeypatch, _SUBJECT)
    assert r.status_code == 200
    body = r.json()
    # Existing member row with no names -> still routed to completion.
    assert body["profile_complete"] is False
    assert body["has_account"] is True
    await _cleanup(email)
