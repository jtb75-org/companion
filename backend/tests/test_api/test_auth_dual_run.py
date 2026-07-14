"""DUAL-RUN auth switch (Firebase <-> Authentik BFF session).

Exercises app/auth/principal.py + the rewired dependencies (get_current_user,
get_current_user_allow_inactive, get_current_admin) directly, with a constructed
Starlette Request and a real Postgres session (the by-subject resolve reads
users.external_subject_id under the RLS bootstrap GUC). Firebase, Redis (session
store), and the flag are all mocked/monkeypatched.

THE INVARIANT under test: with auth_provider == "firebase" (DEFAULT) the Authentik
branch is inert — a session cookie is ignored and only the Firebase bearer path runs.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from starlette.requests import Request

import app.auth.dependencies as deps
import app.auth.session as session_module
from app.auth.session import InMemorySessionStore
from app.config import settings
from app.db import session as db_module
from app.models.admin_user import AdminUser
from app.models.enums import AccountStatus
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _no_dev_bypass(monkeypatch):
    """CI runs with COMPANION_DEV_AUTH_BYPASS=true; force it off here so the
    authorization=None cases exercise the real resolver, not the dev mock."""
    monkeypatch.setattr(settings, "dev_auth_bypass", False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(method: str = "GET", *, cookies: dict | None = None,
                  headers: dict | None = None) -> Request:
    hlist: list[tuple[bytes, bytes]] = []
    if headers:
        for k, v in headers.items():
            hlist.append((k.lower().encode(), v.encode()))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        hlist.append((b"cookie", cookie_str.encode()))
    scope = {
        "type": "http",
        "method": method,
        "headers": hlist,
        "query_string": b"",
        "path": "/",
    }
    return Request(scope)


def _patch_firebase(monkeypatch, email: str | None):
    """Stub the Firebase verifier used by _extract_bearer_token in dependencies."""

    async def _fake_verify(token: str):  # noqa: ARG001
        return {"email": email} if email else {}

    monkeypatch.setattr(deps, "verify_firebase_token", _fake_verify)


def _enable_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    return store


async def _add_user(email: str, *, sub: str | None = None,
                    status=AccountStatus.ACTIVE) -> uuid.UUID:
    async with db_module.async_session_factory() as s:
        u = User(
            email=email,
            preferred_name="P",
            display_name="P",
            account_status=status,
            external_subject_id=sub,
        )
        s.add(u)
        await s.commit()
        return u.id


async def _add_admin(email: str):
    async with db_module.async_session_factory() as s:
        s.add(AdminUser(email=email, name="A", role="admin", is_active=True))
        await s.commit()


async def _cleanup(*emails: str):
    async with db_module.async_session_factory() as s:
        for email in emails:
            await s.execute(delete(AdminUser).where(AdminUser.email == email))
            await s.execute(delete(User).where(User.email == email))
        await s.commit()


# ---------------------------------------------------------------------------
# (a) INVARIANT: flag == "firebase" → Authentik session is ignored
# ---------------------------------------------------------------------------


async def test_firebase_default_ignores_session(monkeypatch):
    """A valid-looking session cookie is inert when auth_provider == 'firebase':
    only the Firebase bearer path runs."""
    assert settings.auth_provider == "firebase"  # sanity: default
    a_email = f"dual-a-{uuid.uuid4()}@t.io"
    b_email = f"dual-b-{uuid.uuid4()}@t.io"
    await _cleanup(a_email, b_email)
    await _add_user(a_email, sub="sub-a")
    await _add_user(b_email)
    # Session store points at A's subject, but the flag is firebase so it's ignored.
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create("sub-a")
    # Firebase bearer resolves B; the session cookie for A must NOT win.
    _patch_firebase(monkeypatch, b_email)
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization="Bearer x", db=db)
    assert user.email == b_email  # Firebase path, not the session
    await _cleanup(a_email, b_email)


async def test_firebase_default_session_without_bearer_401(monkeypatch):
    """flag=firebase + session cookie but NO bearer → 401 (session ignored)."""
    email = f"dual-nobearer-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-nb")
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create("sub-nb")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401
    await _cleanup(email)


# ---------------------------------------------------------------------------
# (b) flag == "authentik": session resolves; Firebase still accepted as fallback
# ---------------------------------------------------------------------------


async def test_authentik_session_resolves_member(monkeypatch):
    email = f"dual-sess-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-sess")
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-sess")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=None, db=db)
    assert user.email == email
    await _cleanup(email)


async def test_authentik_prefers_session_over_bearer(monkeypatch):
    """Both a session and a Firebase bearer present → the session wins."""
    a_email = f"dual-pref-a-{uuid.uuid4()}@t.io"
    b_email = f"dual-pref-b-{uuid.uuid4()}@t.io"
    await _cleanup(a_email, b_email)
    await _add_user(a_email, sub="sub-pref-a")
    await _add_user(b_email)
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-pref-a")
    _patch_firebase(monkeypatch, b_email)  # bearer would resolve B
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization="Bearer x", db=db)
    assert user.email == a_email  # session preferred
    await _cleanup(a_email, b_email)


async def test_authentik_firebase_fallback_still_works(monkeypatch):
    """flag=authentik, NO session cookie → Firebase bearer fallback resolves."""
    email = f"dual-fallback-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email)
    _enable_authentik(monkeypatch)
    _patch_firebase(monkeypatch, email)
    req = _make_request()  # no cookie
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization="Bearer x", db=db)
    assert user.email == email
    await _cleanup(email)


async def test_authentik_no_session_no_bearer_401(monkeypatch):
    _enable_authentik(monkeypatch)
    req = _make_request()
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


async def test_authentik_invalid_session_no_bearer_401(monkeypatch):
    """A session cookie that doesn't map to a live session → fall through to
    Firebase; with no bearer that's a 401."""
    _enable_authentik(monkeypatch)
    req = _make_request(cookies={settings.session_cookie_name: "nonexistent-sid"})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


async def test_authentik_session_unknown_subject_401(monkeypatch):
    """A live session whose subject maps to NO member row is an anomaly → 401
    (invite-only: no auto-provision)."""
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-orphan-none")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# CSRF: session-authenticated unsafe methods require the double-submit token
# ---------------------------------------------------------------------------


async def test_authentik_session_post_requires_csrf(monkeypatch):
    email = f"dual-csrf-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-csrf")
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-csrf")
    # POST with a session cookie but no CSRF header/cookie → 403.
    req = _make_request("POST", cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 403
    # POST WITH a matching double-submit token → resolves.
    req_ok = _make_request(
        "POST",
        cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "tok"},
        headers={"X-CSRF-Token": "tok"},
    )
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req_ok, authorization=None, db=db)
    assert user.email == email
    await _cleanup(email)


# ---------------------------------------------------------------------------
# (b2) MOBILE: the SAME opaque session id presented as Authorization: Bearer
# ---------------------------------------------------------------------------


async def test_authentik_bearer_session_resolves_member(monkeypatch):
    """flag=authentik, no cookie: a session id in ``Authorization: Bearer`` resolves
    the member (the mobile BFF path)."""
    email = f"dual-bearer-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-bearer")
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-bearer")
    req = _make_request(headers={"Authorization": f"Bearer {sid}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == email
    await _cleanup(email)


async def test_authentik_firebase_jwt_not_misresolved_as_session(monkeypatch):
    """flag=authentik: a Firebase id_token (dotted JWT) in Authorization is NOT a
    session key → misses the store → falls through to the Firebase verification."""
    email = f"dual-fbjwt-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email)  # no sub — only resolvable via Firebase email
    _enable_authentik(monkeypatch)
    _patch_firebase(monkeypatch, email)
    jwt_like = "eyJhbGc.eyJzdWI.sig"  # has dots; never a token_urlsafe session id
    req = _make_request(headers={"Authorization": f"Bearer {jwt_like}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {jwt_like}", db=db)
    assert user.email == email  # resolved by the Firebase path, not as a session
    await _cleanup(email)


async def test_authentik_bearer_session_post_no_csrf_required(monkeypatch):
    """A BEARER session is non-ambient, so an unsafe method needs NO CSRF — unlike the
    cookie session (test_authentik_session_post_requires_csrf), which 403s without it."""
    email = f"dual-bearer-csrf-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-bearer-csrf")
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-bearer-csrf")
    # POST via bearer, no CSRF header/cookie at all → still resolves.
    req = _make_request("POST", headers={"Authorization": f"Bearer {sid}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == email
    await _cleanup(email)


async def test_authentik_invalid_bearer_falls_through_to_firebase(monkeypatch):
    """flag=authentik: a bearer that is neither a live session nor a valid Firebase
    token → None → Firebase path; with no email claim that's a 401."""
    _enable_authentik(monkeypatch)
    _patch_firebase(monkeypatch, None)  # Firebase yields no email
    req = _make_request(headers={"Authorization": "Bearer not-a-live-session"})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization="Bearer not-a-live-session", db=db)
    assert ei.value.status_code == 401


async def test_firebase_default_ignores_bearer_session(monkeypatch):
    """flag=firebase: a valid session id presented as a bearer is inert — the Firebase
    bearer verification runs instead (session store never consulted)."""
    assert settings.auth_provider == "firebase"  # sanity: default
    a_email = f"dual-bs-a-{uuid.uuid4()}@t.io"
    b_email = f"dual-bs-b-{uuid.uuid4()}@t.io"
    await _cleanup(a_email, b_email)
    await _add_user(a_email, sub="sub-bs-a")
    await _add_user(b_email)
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create("sub-bs-a")  # would resolve A as a session
    _patch_firebase(monkeypatch, b_email)  # Firebase resolves B
    req = _make_request(headers={"Authorization": f"Bearer {sid}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == b_email  # Firebase path won; the session was ignored
    await _cleanup(a_email, b_email)


# ---------------------------------------------------------------------------
# (c) admin dual path
# ---------------------------------------------------------------------------


async def test_admin_session_resolves(monkeypatch):
    """flag=authentik: a session for a member whose email matches an AdminUser
    resolves the admin (subject -> member email -> AdminUser)."""
    email = f"dual-admin-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-admin")  # session member row
    await _add_admin(email)
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-admin")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        admin = await deps.get_current_admin(req, authorization=None, db=db)
    assert admin.email == email
    await _cleanup(email)


async def test_admin_firebase_path_unchanged(monkeypatch):
    """flag=firebase: admin resolves via the Firebase bearer, session ignored."""
    email = f"dual-admin-fb-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_admin(email)
    _patch_firebase(monkeypatch, email)
    req = _make_request()
    async with db_module.async_session_factory() as db:
        admin = await deps.get_current_admin(req, authorization="Bearer x", db=db)
    assert admin.email == email
    await _cleanup(email)


# ---------------------------------------------------------------------------
# (d) inactive accounts refused on BOTH paths
# ---------------------------------------------------------------------------


async def test_inactive_refused_on_session(monkeypatch):
    email = f"dual-inactive-sess-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-inact", status=AccountStatus.DEACTIVATED)
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-inact")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 403
    await _cleanup(email)


async def test_inactive_refused_on_firebase(monkeypatch):
    email = f"dual-inactive-fb-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, status=AccountStatus.DEACTIVATED)
    _patch_firebase(monkeypatch, email)
    req = _make_request()
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization="Bearer x", db=db)
    assert ei.value.status_code == 403
    await _cleanup(email)


async def test_allow_inactive_session_permits_deactivated(monkeypatch):
    """get_current_user_allow_inactive admits a deactivated member via the session
    (reactivation / cancel-deletion path), mirroring the Firebase behavior."""
    email = f"dual-allowinact-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-allowinact", status=AccountStatus.DEACTIVATED)
    store = _enable_authentik(monkeypatch)
    sid = await store.create("sub-allowinact")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user_allow_inactive(req, authorization=None, db=db)
    assert user.email == email
    await _cleanup(email)
