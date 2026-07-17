"""/api/v1/auth/check must work for EVERY cohort, not just members.

REGRESSION: at the Authentik cutover this endpoint resolved the session with
``resolve_session_principal``, which is MEMBER-ONLY and raises 401 for any subject with
no ``users`` row. A pure admin (joe.buhr@gmail.com — an admin_users row, deliberately no
member row) could therefore log in fine (POST /auth/login -> 200) and then have EVERY
/auth/check 401 with "Session does not map to a known member", so the web dashboard
treated them as unauthenticated and login looked broken. Found in prod by the owner.

The endpoint's whole job is "who is this and what may they do?" for every cohort, and
``authorize_by_email`` is the part that knows about admins/caregivers — so the session
must be resolved ROLE-AGNOSTICALLY (``resolve_session_email``) and never behind a member
lookup. The handler already expects member-less callers: it reports has_account=False /
profile_complete=True for them.

The admin case is the one that broke in prod; the caregiver case covers the other
member-less web cohort a member-only resolver would also reject.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from app.auth import principal as principal_module
from app.config import settings
from app.db import session as db_module
from app.main import app
from app.models.admin_user import AdminUser
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db

_SUBJECT = "test-subject-auth-check-cohorts"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _enable_authentik(monkeypatch, *, subject: str | None) -> None:
    """Turn the switch on and pretend the request carries a session for ``subject``."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    monkeypatch.setattr(settings, "dev_auth_bypass", False)

    async def _fake_subject(_request):
        return subject

    # Patch the seam every resolver funnels through, so we exercise the real
    # _email_for_subject cohort walk without minting a Redis session.
    monkeypatch.setattr(principal_module, "resolve_session_subject", _fake_subject)


async def _cleanup(email: str) -> None:
    from sqlalchemy import delete

    async with db_module.maintenance_session() as mdb:
        await mdb.execute(delete(AdminUser).where(AdminUser.email == email))
        await mdb.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == email)
        )
        await mdb.execute(delete(User).where(User.email == email))
        await mdb.commit()


@requires_db
async def test_pure_admin_session_passes_auth_check(monkeypatch):
    """An admin with NO member row must authenticate — the exact prod lockout."""
    email = f"admin-cohort-{uuid.uuid4().hex[:8]}@example.invalid"
    await _cleanup(email)
    async with db_module.maintenance_session() as mdb:
        mdb.add(
            AdminUser(
                email=email,
                name="Cohort Admin",
                role="admin",
                is_active=True,
                external_subject_id=_SUBJECT,
            )
        )
        await mdb.commit()

    _enable_authentik(monkeypatch, subject=_SUBJECT)
    try:
        async with _client() as ac:
            r = await ac.get("/api/v1/auth/check")
        assert r.status_code == 200, (
            f"pure admin got {r.status_code} {r.text} — the member-only session resolver "
            "is back, which locks admins out of the dashboard"
        )
        body = r.json()
        assert body["authorized"] is True
        assert body["role"] == "admin"
        assert body["email"] == email
        # A pure admin has no member row, and that is CORRECT — not an error state.
        assert body["has_account"] is False
        assert body["profile_complete"] is True
    finally:
        await _cleanup(email)


@requires_db
async def test_pure_caregiver_session_passes_auth_check(monkeypatch):
    """The other web cohort that a member-only resolver would reject.

    NB: a plain member is deliberately NOT tested — ``authorize_by_email`` authorizes
    admin_users then trusted_contacts, so a member who is neither is correctly 403'd
    here. This endpoint serves the WEB dashboard (admins + caregivers); members use the
    mobile app.
    """
    cg_email = f"cg-cohort-{uuid.uuid4().hex[:8]}@example.invalid"
    member_email = f"cg-member-{uuid.uuid4().hex[:8]}@example.invalid"
    await _cleanup(cg_email)
    await _cleanup(member_email)
    async with db_module.maintenance_session() as mdb:
        member = User(
            email=member_email,
            preferred_name="Held",
            display_name="Held Member",
            account_status=AccountStatus.ACTIVE,
        )
        mdb.add(member)
        await mdb.flush()
        mdb.add(
            TrustedContact(
                user_id=member.id,
                contact_name="Cohort Caregiver",
                contact_email=cg_email,
                relationship_type="family",
                access_tier="tier_1",
                is_active=True,
                external_subject_id=_SUBJECT,
            )
        )
        await mdb.commit()

    _enable_authentik(monkeypatch, subject=_SUBJECT)
    try:
        async with _client() as ac:
            r = await ac.get("/api/v1/auth/check")
        assert r.status_code == 200, (
            f"pure caregiver got {r.status_code} {r.text} — a caregiver with no member "
            "row must still resolve"
        )
        body = r.json()
        assert body["authorized"] is True
        assert body["role"] == "caregiver"
        assert body["has_account"] is False  # no member row — correct, not an error
    finally:
        await _cleanup(cg_email)
        await _cleanup(member_email)


@requires_db
async def test_unknown_subject_still_falls_back_and_401s(monkeypatch):
    """A session whose subject maps to no account must NOT be admitted."""
    _enable_authentik(monkeypatch, subject="subject-that-maps-to-nobody")
    async with _client() as ac:
        r = await ac.get("/api/v1/auth/check")
    # No session email -> Firebase fallback -> no bearer -> 401.
    assert r.status_code == 401
