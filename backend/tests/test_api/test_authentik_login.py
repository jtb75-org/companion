"""BFF /auth/login endpoint: gating + Companion's invite-only refusal.

Authentik and Redis are mocked — we never reach a live IdP or Redis. Authentik is the
sole provider (auth_provider defaults to "authentik"); as defense-in-depth the surface
still 404s if the provider is somehow not "authentik". The DB-backed tests assert the
invite-only gate mirrors complete_profile.
"""

from __future__ import annotations

import app.auth.ratelimit as ratelimit_module
import app.auth.session as session_module
from app.auth.oidc import VerifiedToken
from app.auth.ratelimit import InMemoryRateLimiter
from app.auth.session import InMemorySessionStore

_LOGIN = "/auth/login"


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── gating: no DB / Redis needed (the gate fires before anything else) ──

async def test_default_provider_is_authentik():
    """Authentik is the sole provider — the default must be 'authentik' (Firebase auth
    was retired; a non-authentik default would leave the app with no auth path)."""
    from app.config import settings

    assert settings.auth_provider == "authentik"


async def test_login_404_when_provider_not_authentik(monkeypatch):
    """Defense-in-depth: the BFF surface 404s if auth_provider is somehow not 'authentik'
    (the prod startup guard prevents this configuration from ever booting)."""
    from app.config import settings

    monkeypatch.setattr(settings, "auth_provider", "disabled")
    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": "a@b.com", "password": "x"})
    assert r.status_code == 404


async def test_logout_404_when_provider_not_authentik(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_provider", "disabled")
    async with _client() as ac:
        r = await ac.post("/auth/logout")
    assert r.status_code == 404


# ── invite-only + success: DB-backed, IdP + Redis mocked ──

from sqlalchemy import delete, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session as db_module  # noqa: E402
from app.models.admin_user import AdminUser  # noqa: E402
from app.models.audit import AccountAuditLog, CaregiverActivityLog  # noqa: E402
from app.models.enums import (  # noqa: E402
    AccessTier,
    AccountStatus,
    CaregiverAction,
    RelationshipType,
)
from app.models.trusted_contact import TrustedContact  # noqa: E402
from app.models.user import User  # noqa: E402
from tests.conftest import requires_db  # noqa: E402


async def _delete_admin(email: str):
    async with db_module.async_session_factory() as s:
        await s.execute(delete(AdminUser).where(AdminUser.email == email))
        await s.commit()


async def _seed_admin(
    email: str, *, subject: str | None = None, is_active: bool = True, role: str = "admin"
):
    """Create (or replace) an admin_users row. Admins have NO users row."""
    async with db_module.async_session_factory() as s:
        await s.execute(delete(AdminUser).where(AdminUser.email == email))
        s.add(
            AdminUser(
                email=email,
                name="Ad Min",
                role=role,
                is_active=is_active,
                external_subject_id=subject,
            )
        )
        await s.commit()


async def _seed_member_with_caregiver(
    member_email: str, cg_email: str, *, cg_subject: str | None = None
):
    """Create an active member + an active TrustedContact for cg_email. Returns the
    member id. Deleting the member cascades the contact (FK ON DELETE CASCADE)."""
    async with db_module.async_session_factory() as s:
        # Defensive: drop any orphaned contact rows for this caregiver email so a
        # prior failed run's residue can't perturb by-email scalar_one() lookups.
        await s.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == cg_email)
        )
        await s.commit()
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
                contact_name="Care Giver",
                contact_email=cg_email,
                relationship_type=RelationshipType.FAMILY,
                access_tier=AccessTier.TIER_2,
                is_active=True,
                external_subject_id=cg_subject,
            )
        )
        await s.commit()
        return member.id


def _enable_authentik_with_mocks(monkeypatch, *, sub: str, email: str,
                                 email_verified: bool = True):
    """Flip the flag on and stub the IdP flow + verifier + Redis-backed stores."""
    monkeypatch.setattr("app.api.auth_authentik.settings.auth_provider", "authentik")

    class _FakeAuthenticator:
        async def authenticate(self, username, password):  # noqa: ARG002
            from app.auth.authentik_flow import TokenResult

            return TokenResult(id_token="fake-id-token", access_token=None)

    class _FakeVerifier:
        def verify(self, token, *, require_issuer=True):  # noqa: ARG002
            return VerifiedToken(
                sub=sub, email=email, name="T", claims={}, email_verified=email_verified
            )

    monkeypatch.setattr("app.api.auth_authentik._authenticator", lambda: _FakeAuthenticator())
    monkeypatch.setattr(
        "app.api.auth_authentik.get_authentik_verifier", lambda: _FakeVerifier()
    )
    # In-memory doubles so no live Redis is required.
    limiter = InMemoryRateLimiter()
    store = InMemorySessionStore()
    monkeypatch.setattr(ratelimit_module, "_limiter", limiter)
    monkeypatch.setattr(session_module, "_store", store)
    return limiter, store


async def _delete_user(email: str):
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.commit()


@requires_db
async def test_login_refuses_uninvited_email(monkeypatch):
    email = "authentik-uninvited@example.com"
    await _delete_user(email)
    _enable_authentik_with_mocks(monkeypatch, sub="sub-uninvited", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    # No account was auto-provisioned.
    async with db_module.async_session_factory() as s:
        assert (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none() is None
        events = (
            await s.execute(select(AccountAuditLog).where(AccountAuditLog.email == email))
        ).scalars().all()
    assert "signup_refused" in [e.event for e in events]
    await _delete_user(email)


@requires_db
async def test_login_succeeds_for_invited_stub(monkeypatch):
    email = "authentik-invited@example.com"
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
    _, store = _enable_authentik_with_mocks(monkeypatch, sub="sub-invited", email=email)

    # Mobile client (mobile=true): the bearer session token comes in the BODY, and NO
    # cookies are set. A native HTTP stack (NSURLSession/OkHttp) auto-persists any
    # Set-Cookie and re-sends it, which would force the CSRF-enforced cookie path onto the
    # bearer client's state-changing requests and 403 them — so a mobile login must not
    # mint cookies at all.
    async with _client() as ac:
        r = await ac.post(
            _LOGIN, json={"username": email, "password": "pw", "mobile": True}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # No session/CSRF cookies for a mobile login (the fix — see resolve_session_subject).
    cookie_names = {c for c in r.cookies}
    assert "companion_sid" not in cookie_names
    assert "companion_csrf" not in cookie_names
    # Mobile bearer: the body carries the opaque session id, which maps to the Authentik
    # subject (no email/PII stored). csrf_token is present for parity though the app
    # ignores it (a bearer is non-ambient and needs no CSRF).
    sid = body["session_token"]
    assert await store.get(sid) == "sub-invited"
    assert body["csrf_token"]
    await _delete_user(email)


@requires_db
async def test_login_web_omits_session_token_from_body(monkeypatch):
    """Web clients (mobile not set) get the SESSION only via the httpOnly cookie — the
    opaque sid must never appear in the JSON body where browser JS could read it,
    preserving the httpOnly/XSS posture. The csrf token IS returned in the body (it is
    already a readable/non-httpOnly value) so the cross-subdomain SPA can echo it as
    X-CSRF-Token without reading the host-only cookie."""
    email = "authentik-web@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="W",
                display_name="W",
                account_status=AccountStatus.INVITED,
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="sub-web", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 200
    body = r.json()
    # The sid must NOT be in the body; the csrf token MUST be, and match the cookie.
    assert "session_token" not in body
    assert body["csrf_token"]
    assert body["csrf_token"] == r.cookies["companion_csrf"]
    assert "companion_sid" in {c for c in r.cookies}
    # The session still exists — delivered via the httpOnly cookie only.
    assert "companion_sid" in {c for c in r.cookies}
    await _delete_user(email)


@requires_db
async def test_login_refuses_deactivated_account(monkeypatch):
    email = "authentik-deactivated@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="D",
                display_name="D",
                account_status=AccountStatus.DEACTIVATED,
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="sub-deact", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    await _delete_user(email)


async def test_logout_revokes_bearer_session(monkeypatch):
    """Mobile logout: a bearer-only /auth/logout (no cookie jar) must delete the
    server-side session, so a copied/stolen bearer sid cannot outlive logout
    until its TTL (niru finding on the #74/#75 pair)."""
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub="sub-logout", email="logout@example.com"
    )
    sid = await store.create("sub-logout")
    assert await store.get(sid) == "sub-logout"
    async with _client() as ac:
        r = await ac.post("/auth/logout", headers={"Authorization": f"Bearer {sid}"})
    assert r.status_code == 204
    # The session is gone from the store — the bearer is now dead server-side.
    assert await store.get(sid) is None


async def test_logout_revokes_cookie_session(monkeypatch):
    """Web logout still revokes the cookie-borne session (regression guard)."""
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub="sub-cookie", email="cookie@example.com"
    )
    sid = await store.create("sub-cookie")
    async with _client() as ac:
        r = await ac.post(
            "/auth/logout", cookies={settings.session_cookie_name: sid}
        )
    assert r.status_code == 204
    assert await store.get(sid) is None


@requires_db
async def test_login_refuses_unverified_email(monkeypatch):
    """Cutover gate #5: an id_token whose email is present but NOT verified is refused
    before any invite-only resolution / backfill / session mint."""
    email = "authentik-unverified@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="U",
                display_name="U",
                account_status=AccountStatus.ACTIVE,
                external_subject_id="sub-unverified",
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(
        monkeypatch, sub="sub-unverified", email=email, email_verified=False
    )

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    await _delete_user(email)


# ── PR #3: stable-subject resolution + lazy backfill ──


@requires_db
async def test_login_resolves_by_subject(monkeypatch):
    """A member whose external_subject_id is already set resolves by SUB, not email.

    We give the row a distinct email so an email lookup would miss — proving the
    by-subject path is what admits the login."""
    email = "authentik-bysub@example.com"
    sub = "sub-already-bound"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="S",
                display_name="S",
                account_status=AccountStatus.ACTIVE,
                external_subject_id=sub,
            )
        )
        await s.commit()
    # Token carries a DIFFERENT email claim; only the sub matches the stored row.
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub=sub, email="not-the-stored-email@example.com"
    )

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 200
    sid = r.cookies["companion_sid"]
    assert await store.get(sid) == sub
    await _delete_user(email)


@requires_db
async def test_login_backfills_subject_on_first_login(monkeypatch):
    """First Authentik login of an invited member: resolved by email, then the
    stable subject is lazily persisted to external_subject_id."""
    email = "authentik-backfill@example.com"
    sub = "sub-backfilled"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="B",
                display_name="B",
                account_status=AccountStatus.INVITED,
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub=sub, email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 200
    # The mapping was written to the column.
    async with db_module.async_session_factory() as s:
        row = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert row.external_subject_id == sub
    await _delete_user(email)


@requires_db
async def test_login_refuses_subject_mismatch(monkeypatch):
    """An existing row bound to subject A must not be overwritten by token subject
    B arriving on the same email — refuse (a member's stable subject can't change)."""
    email = "authentik-mismatch@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="M",
                display_name="M",
                account_status=AccountStatus.ACTIVE,
                external_subject_id="sub-original",
            )
        )
        await s.commit()
    # Token's sub does NOT match the stored one, but the email does.
    _enable_authentik_with_mocks(monkeypatch, sub="sub-attacker", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    # The stored subject was NOT overwritten.
    async with db_module.async_session_factory() as s:
        row = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert row.external_subject_id == "sub-original"
    await _delete_user(email)


@requires_db
async def test_login_refuses_empty_subject(monkeypatch):
    """An id_token with an empty sub is refused before any lookup/backfill/session
    (safety-reviewer follow-up #4: sub non-emptiness)."""
    email = "authentik-emptysub@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="E",
                display_name="E",
                account_status=AccountStatus.ACTIVE,
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 401
    # No backfill happened.
    async with db_module.async_session_factory() as s:
        row = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        assert row.external_subject_id is None
    await _delete_user(email)


# ── caregiver wave: caregiver login admission + subject backfill + resolver ──


@requires_db
async def test_login_admits_active_caregiver(monkeypatch):
    """A verified email that is NOT a member but IS an active trusted contact gets a
    BFF session, and the caregiver subject is lazy-backfilled onto the contact row."""
    member_email = "cg-owner@example.com"
    cg_email = "caregiver-admit@example.com"
    await _delete_user(member_email)
    await _seed_member_with_caregiver(member_email, cg_email)
    _, store = _enable_authentik_with_mocks(monkeypatch, sub="sub-cg-admit", email=cg_email)

    async with _client() as ac:
        r = await ac.post(
            _LOGIN, json={"username": cg_email, "password": "pw", "mobile": True}
        )
    assert r.status_code == 200
    sid = r.json()["session_token"]
    # Session maps to the caregiver's opaque subject (no member row exists for them).
    assert await store.get(sid) == "sub-cg-admit"
    # The subject was lazy-backfilled onto the caregiver's active contact row.
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.external_subject_id == "sub-cg-admit"
    await _delete_user(member_email)


@requires_db
async def test_login_refuses_caregiver_subject_mismatch(monkeypatch):
    """A caregiver contact already bound to a DIFFERENT subject is refused, not
    overwritten (mirrors the member subject-mismatch guard)."""
    member_email = "cg-owner2@example.com"
    cg_email = "caregiver-mismatch@example.com"
    await _delete_user(member_email)
    await _seed_member_with_caregiver(
        member_email, cg_email, cg_subject="sub-original"
    )
    _enable_authentik_with_mocks(monkeypatch, sub="sub-attacker", email=cg_email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": cg_email, "password": "pw"})
    assert r.status_code == 403
    # The original binding is untouched (the mismatch rolled back).
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.external_subject_id == "sub-original"
    await _delete_user(member_email)


@requires_db
async def test_caregiver_session_lists_charges(monkeypatch):
    """End-to-end: a caregiver BFF session resolves to the verified email and
    /my-charges returns the member — no Firebase token involved."""
    member_email = "cg-owner3@example.com"
    cg_email = "caregiver-charges@example.com"
    await _delete_user(member_email)
    member_id = await _seed_member_with_caregiver(
        member_email, cg_email, cg_subject="sub-cg-charges"
    )
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub="sub-cg-charges", email=cg_email
    )
    sid = await store.create("sub-cg-charges")

    async with _client() as ac:
        r = await ac.get(
            "/api/v1/auth/my-charges",
            cookies={settings.session_cookie_name: sid},
        )
    assert r.status_code == 200
    # The caregiver session resolved to the verified email and /my-charges returned
    # this caregiver's member (a caregiver may serve several, so assert membership).
    charges = r.json()["charges"]
    assert str(member_id) in [c["user_id"] for c in charges]
    await _delete_user(member_email)


@requires_db
async def test_login_refuses_unverified_caregiver_email(monkeypatch):
    """An UNVERIFIED email that matches an active caregiver row is refused before the
    caregiver branch runs (the email_verified gate precedes admission), and no subject
    is bound (safety follow-up)."""
    member_email = "cg-owner5@example.com"
    cg_email = "caregiver-unverified@example.com"
    await _delete_user(member_email)
    await _seed_member_with_caregiver(member_email, cg_email)
    _enable_authentik_with_mocks(
        monkeypatch, sub="sub-cg-unverif", email=cg_email, email_verified=False
    )

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": cg_email, "password": "pw"})
    assert r.status_code == 403
    # The email_verified gate fired first — no subject was bound to the caregiver row.
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.external_subject_id is None
    await _delete_user(member_email)


# ── admin wave: pure-admin login admission + subject backfill + resolution ──


@requires_db
async def test_login_admits_active_admin(monkeypatch):
    """A verified email that is NOT a member/caregiver but IS an active admin gets a
    BFF session, and the subject is lazy-backfilled onto the admin_users row."""
    email = "pure-admin@example.com"
    await _seed_admin(email)
    _, store = _enable_authentik_with_mocks(monkeypatch, sub="sub-admin", email=email)

    async with _client() as ac:
        r = await ac.post(
            _LOGIN, json={"username": email, "password": "pw", "mobile": True}
        )
    assert r.status_code == 200
    sid = r.json()["session_token"]
    assert await store.get(sid) == "sub-admin"
    async with db_module.async_session_factory() as s:
        admin = (
            await s.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one()
        assert admin.external_subject_id == "sub-admin"
    await _delete_admin(email)


@requires_db
async def test_login_refuses_inactive_admin(monkeypatch):
    """An inactive admin is refused (parity with get_current_admin) and not bound."""
    email = "inactive-admin@example.com"
    await _seed_admin(email, is_active=False)
    _enable_authentik_with_mocks(monkeypatch, sub="sub-inactive-admin", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    async with db_module.async_session_factory() as s:
        admin = (
            await s.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one()
        assert admin.external_subject_id is None
    await _delete_admin(email)


@requires_db
async def test_login_refuses_admin_subject_mismatch(monkeypatch):
    """An admin already bound to a DIFFERENT subject is refused, not overwritten."""
    email = "mismatch-admin@example.com"
    await _seed_admin(email, subject="sub-original-admin")
    _enable_authentik_with_mocks(monkeypatch, sub="sub-attacker-admin", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    async with db_module.async_session_factory() as s:
        admin = (
            await s.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one()
        assert admin.external_subject_id == "sub-original-admin"
    await _delete_admin(email)


@requires_db
async def test_admin_session_authorizes_admin_endpoint(monkeypatch):
    """End-to-end: a pure-admin BFF session (no users row) resolves via
    get_current_admin and reaches an admin endpoint."""
    email = "e2e-admin@example.com"
    await _seed_admin(email, subject="sub-e2e-admin", role="admin")
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub="sub-e2e-admin", email=email
    )
    sid = await store.create("sub-e2e-admin")

    async with _client() as ac:
        r = await ac.get(
            "/admin/config", cookies={settings.session_cookie_name: sid}
        )
    assert r.status_code == 200
    await _delete_admin(email)


@requires_db
async def test_role_overlap_admin_resolves_via_caregiver_subject(monkeypatch):
    """Role-agnostic resolution (niru): a person who is BOTH an active caregiver and an
    active admin logs in via the caregiver branch (which backfills trusted_contacts
    only), yet a later admin request still resolves via the shared subject→email lookup
    and reaches the admin endpoint."""
    email = "overlap@example.com"
    member_email = "overlap-owner@example.com"
    await _delete_user(member_email)
    await _delete_admin(email)
    # Active caregiver contact for `email`, already bound to the subject (as the
    # caregiver login branch would have left it); admin row NOT backfilled.
    await _seed_member_with_caregiver(member_email, email, cg_subject="sub-overlap")
    await _seed_admin(email)
    _, store = _enable_authentik_with_mocks(
        monkeypatch, sub="sub-overlap", email=email
    )
    sid = await store.create("sub-overlap")

    async with _client() as ac:
        r = await ac.get(
            "/admin/config", cookies={settings.session_cookie_name: sid}
        )
    assert r.status_code == 200
    await _delete_admin(email)
    await _delete_user(member_email)


# ── pre-PHI: caregiver activity logging (docs §5) ──


@requires_db
async def test_caregiver_dashboard_view_is_logged(monkeypatch):
    """A Tier-2 dashboard view writes an append-only CaregiverActivityLog with the
    VIEWED_DASHBOARD action and the caregiver's contact id (docs §5). The seeded
    member has no prior activity, so exactly one row must appear."""
    member_email = "log-owner@example.com"
    cg_email = "log-caregiver@example.com"
    await _delete_user(member_email)
    member_id = await _seed_member_with_caregiver(
        member_email, cg_email, cg_subject="sub-log-cg"
    )
    _, store = _enable_authentik_with_mocks(monkeypatch, sub="sub-log-cg", email=cg_email)
    # Force the REAL caregiver-session path: CI sets dev_auth_bypass=true, and the
    # dashboard dev-bypass branch (no Authorization header) returns the summary WITHOUT
    # logging. We need the authenticated path that actually writes the audit record.
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    sid = await store.create("sub-log-cg")

    async with _client() as ac:
        r = await ac.get(
            f"/api/v1/caregiver/dashboard?user_id={member_id}",
            cookies={settings.session_cookie_name: sid},
        )
    assert r.status_code == 200
    # Read the audit row on the maintenance (BYPASSRLS) session so per-member RLS (028)
    # can't hide it from a context-less assertion read.
    async with db_module.maintenance_session() as s:
        logs = (
            await s.execute(
                select(CaregiverActivityLog).where(
                    CaregiverActivityLog.user_id == member_id
                )
            )
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].action == CaregiverAction.VIEWED_DASHBOARD
    assert logs[0].trusted_contact_id is not None
    # details is structured metadata only — never raw member data.
    assert logs[0].details == {"surface": "dashboard"}
    await _delete_user(member_email)


@requires_db
async def test_caregiver_log_retained_when_contact_deleted(monkeypatch):
    """Revoking a caregiver (deleting the trusted_contact) RETAINS their activity log —
    the row survives with trusted_contact_id NULL + user_id intact (ON DELETE SET NULL,
    docs §5 "Sam can view the full activity log")."""
    member_email = "retain-owner@example.com"
    cg_email = "retain-cg@example.com"
    await _delete_user(member_email)
    member_id = await _seed_member_with_caregiver(
        member_email, cg_email, cg_subject="sub-retain"
    )
    # Write one activity row for (contact, member).
    async with db_module.async_session_factory() as s:
        contact = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        contact_id = contact.id
        s.add(
            CaregiverActivityLog(
                trusted_contact_id=contact_id,
                user_id=member_id,
                action=CaregiverAction.VIEWED_DASHBOARD,
                details={"surface": "dashboard"},
            )
        )
        await s.commit()

    # Revoke the caregiver: delete the contact via the ORM (exercises the relationship).
    async with db_module.async_session_factory() as s:
        contact = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.id == contact_id)
            )
        ).scalar_one()
        await s.delete(contact)
        await s.commit()

    # The log row is RETAINED with the contact link nulled — not cascade-erased.
    async with db_module.async_session_factory() as s:
        logs = (
            await s.execute(
                select(CaregiverActivityLog).where(
                    CaregiverActivityLog.user_id == member_id
                )
            )
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].trusted_contact_id is None
    assert logs[0].user_id == member_id
    assert logs[0].action == CaregiverAction.VIEWED_DASHBOARD
    await _delete_user(member_email)


@requires_db
async def test_contact_delete_emits_no_orm_write_on_append_only_log(monkeypatch):
    """passive_deletes='all' must fully defer to the DB ON DELETE SET NULL: even with
    activity_logs EAGER-LOADED, SQLAlchemy must emit NO UPDATE/DELETE on the append-only
    caregiver_activity_log (companion_app lacks both, #83). Regression for niru's finding
    that passive_deletes=True still UPDATEs a loaded collection. As the owner role an ORM
    UPDATE would silently succeed, so we assert on the emitted SQL, not just the outcome."""
    from sqlalchemy import event
    from sqlalchemy.orm import selectinload

    member_email = "noupd-owner@example.com"
    cg_email = "noupd-cg@example.com"
    await _delete_user(member_email)
    member_id = await _seed_member_with_caregiver(
        member_email, cg_email, cg_subject="sub-noupd"
    )
    async with db_module.async_session_factory() as s:
        contact_id = (
            await s.execute(
                select(TrustedContact.id).where(
                    TrustedContact.contact_email == cg_email
                )
            )
        ).scalar_one()
        s.add(
            CaregiverActivityLog(
                trusted_contact_id=contact_id,
                user_id=member_id,
                action=CaregiverAction.VIEWED_DASHBOARD,
                details={"surface": "dashboard"},
            )
        )
        await s.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db_module.async_session_factory.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with db_module.async_session_factory() as s:
            contact = (
                await s.execute(
                    select(TrustedContact)
                    .options(selectinload(TrustedContact.activity_logs))
                    .where(TrustedContact.id == contact_id)
                )
            ).scalar_one()
            assert contact.activity_logs  # eager-loaded into the session
            await s.delete(contact)
            await s.commit()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    offending = [
        st
        for st in statements
        if st.lower().strip().startswith(
            ("update caregiver_activity_log", "delete from caregiver_activity_log")
        )
    ]
    assert not offending, f"ORM wrote to the append-only log: {offending}"
    # And the row was retained with the contact link nulled by the DB.
    async with db_module.maintenance_session() as s:
        logs = (
            await s.execute(
                select(CaregiverActivityLog).where(
                    CaregiverActivityLog.user_id == member_id
                )
            )
        ).scalars().all()
    assert len(logs) == 1
    assert logs[0].trusted_contact_id is None
    await _delete_user(member_email)


# ── pre-PHI: BFF login audit ──


async def _audit_rows(email: str, event: str):
    async with db_module.async_session_factory() as s:
        return (
            await s.execute(
                select(AccountAuditLog).where(
                    AccountAuditLog.email == email, AccountAuditLog.event == event
                )
            )
        ).scalars().all()


@requires_db
async def test_bff_login_success_is_audited(monkeypatch):
    """A successful member BFF login writes a durable bff_login_success record."""
    email = "audit-member@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="A",
                display_name="A",
                account_status=AccountStatus.ACTIVE,
                external_subject_id="sub-audit",
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="sub-audit", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 200
    rows = await _audit_rows(email, "bff_login_success")
    assert len(rows) == 1
    assert rows[0].details == {"role": "member"}
    await _delete_user(email)


@requires_db
async def test_bff_login_subject_mismatch_is_audited(monkeypatch):
    """A subject mismatch writes a durable audit record that SURVIVES the 403 rollback."""
    email = "audit-mismatch@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="M",
                display_name="M",
                account_status=AccountStatus.ACTIVE,
                external_subject_id="sub-original",
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="sub-attacker", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    rows = await _audit_rows(email, "bff_login_subject_mismatch")
    assert len(rows) == 1
    assert rows[0].details == {"role": "member"}
    await _delete_user(email)


@requires_db
async def test_bff_login_refused_inactive_is_audited(monkeypatch):
    """Valid IdP creds against a DEACTIVATED member write a bff_login_refused record."""
    email = "audit-inactive@example.com"
    await _delete_user(email)
    async with db_module.async_session_factory() as s:
        s.add(
            User(
                email=email,
                preferred_name="I",
                display_name="I",
                account_status=AccountStatus.DEACTIVATED,
                external_subject_id="sub-inactive-aud",
            )
        )
        await s.commit()
    _enable_authentik_with_mocks(monkeypatch, sub="sub-inactive-aud", email=email)

    async with _client() as ac:
        r = await ac.post(_LOGIN, json={"username": email, "password": "pw"})
    assert r.status_code == 403
    rows = await _audit_rows(email, "bff_login_refused")
    assert len(rows) == 1
    assert rows[0].details == {"role": "member", "reason": "inactive_account"}
    await _delete_user(email)
