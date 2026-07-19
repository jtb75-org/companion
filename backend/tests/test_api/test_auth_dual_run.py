"""Authentik BFF session resolution on the auth dependencies.

Exercises app/auth/principal.py + the dependencies (get_current_user,
get_current_user_allow_inactive, get_current_admin) directly, with a constructed
Starlette Request and a real Postgres session (the by-subject resolve reads
users.external_subject_id under the RLS bootstrap GUC). The session store is an
in-memory stub.

Authentik is the sole authentication path (Firebase auth retired): a resolver that
returns ``None`` means "no valid session" and the dependency raises 401 — there is no
fallback.
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


def _session_store(monkeypatch) -> InMemorySessionStore:
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
# Session resolution (cookie)
# ---------------------------------------------------------------------------


async def test_session_resolves_member(monkeypatch):
    email = f"sess-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-sess")
    store = _session_store(monkeypatch)
    sid = await store.create("sub-sess")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=None, db=db)
    assert user.email == email
    await _cleanup(email)


async def test_no_session_401(monkeypatch):
    _session_store(monkeypatch)
    req = _make_request()
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


async def test_invalid_session_401(monkeypatch):
    """A session cookie that doesn't map to a live session → 401."""
    _session_store(monkeypatch)
    req = _make_request(cookies={settings.session_cookie_name: "nonexistent-sid"})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


async def test_session_unknown_subject_401(monkeypatch):
    """A live session whose subject maps to NO member row is an anomaly → 401
    (invite-only: no auto-provision)."""
    store = _session_store(monkeypatch)
    sid = await store.create("sub-orphan-none")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# CSRF: session-authenticated unsafe methods require the double-submit token
# ---------------------------------------------------------------------------


async def test_session_post_requires_csrf(monkeypatch):
    email = f"csrf-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-csrf")
    store = _session_store(monkeypatch)
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
# MOBILE: the SAME opaque session id presented as Authorization: Bearer
# ---------------------------------------------------------------------------


async def test_bearer_session_resolves_member(monkeypatch):
    """No cookie: a session id in ``Authorization: Bearer`` resolves the member (the
    mobile BFF path)."""
    email = f"bearer-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-bearer")
    store = _session_store(monkeypatch)
    sid = await store.create("sub-bearer")
    req = _make_request(headers={"Authorization": f"Bearer {sid}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == email
    await _cleanup(email)


async def test_bearer_session_post_no_csrf_required(monkeypatch):
    """A BEARER session is non-ambient, so an unsafe method needs NO CSRF — unlike the
    cookie session (test_session_post_requires_csrf), which 403s without it."""
    email = f"bearer-csrf-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-bearer-csrf")
    store = _session_store(monkeypatch)
    sid = await store.create("sub-bearer-csrf")
    # POST via bearer, no CSRF header/cookie at all → still resolves.
    req = _make_request("POST", headers={"Authorization": f"Bearer {sid}"})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == email
    await _cleanup(email)


async def test_bearer_wins_over_autoresent_cookie_on_post(monkeypatch):
    """REGRESSION (the prod mobile lockout): a native client presents the session id as a
    BEARER, but its HTTP stack (NSURLSession/OkHttp) ALSO auto-re-sends the login cookie.
    On a state-changing POST with NO X-CSRF-Token — exactly what iOS sends — the request
    must resolve via the bearer (non-ambient, no CSRF), NOT via the cookie, which would
    403 'CSRF token missing or invalid'. Before the bearer-first fix, every mobile POST
    after login returned 403 and onboarding was un-completable.

    Cookie and bearer carry the SAME sid here (the real scenario); the assertion that
    matters is that the POST resolves at all instead of 403-ing."""
    email = f"mobile-both-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-mobile-both")
    store = _session_store(monkeypatch)
    sid = await store.create("sub-mobile-both")
    req = _make_request(
        "POST",
        cookies={settings.session_cookie_name: sid},
        headers={"Authorization": f"Bearer {sid}"},
    )
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user(req, authorization=f"Bearer {sid}", db=db)
    assert user.email == email  # resolved via the bearer — no spurious CSRF 403
    await _cleanup(email)


async def test_cookie_still_csrf_enforced_when_no_bearer(monkeypatch):
    """Guard the OTHER side of the bearer-first swap: a browser (cookie only, NO session
    bearer) on a POST without the double-submit token must STILL 403. Bearer-first must
    not weaken web CSRF — it only bypasses CSRF when a real bearer is actually present."""
    email = f"web-csrf-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-web-csrf")
    store = _session_store(monkeypatch)
    sid = await store.create("sub-web-csrf")
    # Cookie only, POST, no X-CSRF-Token, no Authorization header at all.
    req = _make_request("POST", cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 403
    await _cleanup(email)


async def test_invalid_bearer_401(monkeypatch):
    """A bearer that is not a live session → None → 401 (no fallback)."""
    _session_store(monkeypatch)
    req = _make_request(headers={"Authorization": "Bearer not-a-live-session"})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization="Bearer not-a-live-session", db=db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# Admin session path
# ---------------------------------------------------------------------------


async def test_admin_session_resolves(monkeypatch):
    """A session for a member whose email matches an AdminUser resolves the admin
    (subject -> member email -> AdminUser)."""
    email = f"admin-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-admin")  # session member row
    await _add_admin(email)
    store = _session_store(monkeypatch)
    sid = await store.create("sub-admin")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        admin = await deps.get_current_admin(req, authorization=None, db=db)
    assert admin.email == email
    await _cleanup(email)


async def test_admin_no_session_401(monkeypatch):
    """No session → get_current_admin raises 401 (no fallback)."""
    _session_store(monkeypatch)
    req = _make_request()
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_admin(req, authorization=None, db=db)
    assert ei.value.status_code == 401


# ---------------------------------------------------------------------------
# Inactive accounts
# ---------------------------------------------------------------------------


async def test_inactive_refused_on_session(monkeypatch):
    email = f"inactive-sess-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-inact", status=AccountStatus.DEACTIVATED)
    store = _session_store(monkeypatch)
    sid = await store.create("sub-inact")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    with pytest.raises(HTTPException) as ei:
        async with db_module.async_session_factory() as db:
            await deps.get_current_user(req, authorization=None, db=db)
    assert ei.value.status_code == 403
    await _cleanup(email)


async def test_allow_inactive_session_permits_deactivated(monkeypatch):
    """get_current_user_allow_inactive admits a deactivated member via the session
    (reactivation / cancel-deletion path)."""
    email = f"allowinact-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _add_user(email, sub="sub-allowinact", status=AccountStatus.DEACTIVATED)
    store = _session_store(monkeypatch)
    sid = await store.create("sub-allowinact")
    req = _make_request(cookies={settings.session_cookie_name: sid})
    async with db_module.async_session_factory() as db:
        user = await deps.get_current_user_allow_inactive(req, authorization=None, db=db)
    assert user.email == email
    await _cleanup(email)
