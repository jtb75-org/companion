"""Unit tests for the Authentik account-provisioning integration (PR 1).

HTTP is mocked with ``httpx.MockTransport`` (the repo's httpx convention — no
respx dependency): the module builds its own ``httpx.AsyncClient`` internally, so
we monkeypatch ``authentik_admin.httpx.AsyncClient`` with a factory that injects a
MockTransport handler (and drops the ``verify`` kwarg, which MockTransport ignores).

Coverage:
1. inert when the provider is not authentik — no client is ever constructed;
2. inert when the admin token is empty even under auth_provider=authentik;
3. idempotent — an existing account (GET returns results) issues no POST;
4. creates — GET empty ⇒ POST to /api/v3/core/users/ with the expected body + Bearer;
5. best-effort — a transport error / 500 does NOT raise;
6. (seam) get_or_create_stub_user provisions only when the switch is on.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.config import settings
from app.integrations import authentik_admin
from app.integrations.authentik_admin import (
    delete_authentik_account,
    provision_authentik_account,
    set_authentik_password,
)


class Recorder:
    """Records client instantiations + the requests seen by the MockTransport."""

    def __init__(self) -> None:
        self.instantiations = 0
        self.requests: list[httpx.Request] = []
        self.post_body: dict | None = None
        self.post_auth: str | None = None


def _install_client(monkeypatch, handler, recorder: Recorder) -> None:
    real_cls = httpx.AsyncClient

    def factory(*args, **kwargs):
        recorder.instantiations += 1
        kwargs.pop("verify", None)  # MockTransport supersedes TLS verification
        return real_cls(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(authentik_admin.httpx, "AsyncClient", factory)


def _make_handler(recorder: Recorder, *, existing: list, post_status: int = 201):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": existing})
        # POST
        recorder.post_body = json.loads(request.content)
        recorder.post_auth = request.headers.get("Authorization")
        return httpx.Response(post_status, json={"pk": 1, "username": "x"})

    return handler


def _enable_authentik(monkeypatch, *, token: str = "test-admin-token") -> None:
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    monkeypatch.setattr(settings, "authentik_api_token", token)


# ── 1. inert when the provider is not authentik ─────────────────────────────────
@pytest.mark.asyncio
async def test_inert_when_provider_not_authentik(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    monkeypatch.setattr(settings, "authentik_api_token", "test-admin-token")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    await provision_authentik_account("nobody@example.com", "Nobody")

    assert rec.instantiations == 0  # zero HTTP client built ⇒ zero HTTP
    assert rec.requests == []


# ── 2. inert when the admin token is empty (even under authentik) ────────────────
@pytest.mark.asyncio
async def test_inert_when_token_empty(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    monkeypatch.setattr(settings, "authentik_api_token", "")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    await provision_authentik_account("nobody@example.com", "Nobody")

    assert rec.instantiations == 0
    assert rec.requests == []


# ── 3. idempotent — existing account ⇒ no POST ──────────────────────────────────
@pytest.mark.asyncio
async def test_idempotent_when_account_exists(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()
    handler = _make_handler(rec, existing=[{"pk": 42, "email": "there@example.com"}])
    _install_client(monkeypatch, handler, rec)

    await provision_authentik_account("there@example.com", "Al Ready")

    methods = [r.method for r in rec.requests]
    assert methods == ["GET"]  # GET issued, no POST
    assert rec.post_body is None


# ── 4. creates — GET empty ⇒ POST with the expected body + Bearer ────────────────
@pytest.mark.asyncio
async def test_creates_when_absent(monkeypatch):
    _enable_authentik(monkeypatch, token="tok-abc")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    await provision_authentik_account("new@example.com", "New Person")

    methods = [r.method for r in rec.requests]
    assert methods == ["GET", "POST"]
    post = rec.requests[1]
    assert post.url.path == "/api/v3/core/users/"
    assert rec.post_body == {
        "username": "new@example.com",
        "email": "new@example.com",
        "name": "New Person",
        "type": "internal",
        "is_active": True,
        "path": "users",
    }
    assert rec.post_auth == "Bearer tok-abc"
    # The GET filters by email.
    assert rec.requests[0].url.params.get("email") == "new@example.com"


# ── 5. best-effort — HTTP failure does NOT raise ────────────────────────────────
@pytest.mark.asyncio
async def test_does_not_raise_on_http_error(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()

    def boom(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        raise httpx.ConnectError("authentik unreachable", request=request)

    _install_client(monkeypatch, boom, rec)

    # Must not raise — provisioning is best-effort.
    await provision_authentik_account("x@example.com", "X")
    assert [r.method for r in rec.requests] == ["GET"]


@pytest.mark.asyncio
async def test_does_not_raise_on_5xx(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()

    def five_hundred(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        return httpx.Response(500, json={"detail": "boom"})

    _install_client(monkeypatch, five_hundred, rec)

    await provision_authentik_account("x@example.com", "X")  # no raise
    assert [r.method for r in rec.requests] == ["GET"]  # 500 on GET ⇒ no POST


# ── 5b. set_authentik_password — must-succeed activation (PR 2) ─────────────────
@pytest.mark.asyncio
async def test_set_password_raises_when_not_configured(monkeypatch):
    # No switch / no token ⇒ programming error (only called from the gated endpoint).
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    monkeypatch.setattr(settings, "authentik_api_token", "tok")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)
    with pytest.raises(RuntimeError):
        await set_authentik_password("x@example.com", "pw12345678")
    assert rec.instantiations == 0  # zero HTTP


@pytest.mark.asyncio
async def test_set_password_success(monkeypatch):
    _enable_authentik(monkeypatch, token="tok-pw")
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"pk": 7, "email": "x"}]})
        rec.post_body = json.loads(request.content)
        rec.post_auth = request.headers.get("Authorization")
        return httpx.Response(204)

    _install_client(monkeypatch, handler, rec)

    await set_authentik_password("x@example.com", "s3cret-password")

    assert [r.method for r in rec.requests] == ["GET", "POST"]
    assert rec.requests[1].url.path == "/api/v3/core/users/7/set_password/"
    assert rec.post_body == {"password": "s3cret-password"}
    assert rec.post_auth == "Bearer tok-pw"


@pytest.mark.asyncio
async def test_set_password_raises_when_no_account(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)
    with pytest.raises(RuntimeError):
        await set_authentik_password("missing@example.com", "pw12345678")
    assert [r.method for r in rec.requests] == ["GET"]  # no POST


@pytest.mark.asyncio
async def test_set_password_raises_on_5xx(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"pk": 9}]})
        return httpx.Response(500, json={"detail": "boom"})

    _install_client(monkeypatch, handler, rec)
    with pytest.raises(httpx.HTTPStatusError):
        await set_authentik_password("x@example.com", "pw12345678")


# ── 5c. delete_authentik_account — member account-deletion cleanup ──────────────
@pytest.mark.asyncio
async def test_delete_skipped_when_not_enabled(monkeypatch):
    # Best-effort deletion path: a missing gate is a benign no-op, NOT a raise.
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    monkeypatch.setattr(settings, "authentik_api_token", "tok")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    outcome = await delete_authentik_account("x@example.com")

    assert outcome == "skipped"
    assert rec.instantiations == 0  # zero HTTP


@pytest.mark.asyncio
async def test_delete_skipped_when_token_empty(monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    monkeypatch.setattr(settings, "authentik_api_token", "")
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    outcome = await delete_authentik_account("x@example.com")

    assert outcome == "skipped"
    assert rec.instantiations == 0


@pytest.mark.asyncio
async def test_delete_not_found_when_no_account(monkeypatch):
    _enable_authentik(monkeypatch)
    rec = Recorder()
    _install_client(monkeypatch, _make_handler(rec, existing=[]), rec)

    outcome = await delete_authentik_account("missing@example.com")

    assert outcome == "not_found"
    assert [r.method for r in rec.requests] == ["GET"]  # no DELETE


@pytest.mark.asyncio
async def test_delete_success(monkeypatch):
    _enable_authentik(monkeypatch, token="tok-del")
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"pk": 13, "email": "x"}]})
        # DELETE
        rec.post_auth = request.headers.get("Authorization")
        return httpx.Response(204)

    _install_client(monkeypatch, handler, rec)

    outcome = await delete_authentik_account("x@example.com")

    assert outcome == "deleted"
    assert [r.method for r in rec.requests] == ["GET", "DELETE"]
    assert rec.requests[1].url.path == "/api/v3/core/users/13/"
    assert rec.post_auth == "Bearer tok-del"
    assert rec.requests[0].url.params.get("email") == "x@example.com"


@pytest.mark.asyncio
async def test_delete_raises_on_5xx(monkeypatch):
    # Genuine HTTP errors PROPAGATE — the lifecycle caller wraps them best-effort.
    _enable_authentik(monkeypatch)
    rec = Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"pk": 9}]})
        return httpx.Response(500, json={"detail": "boom"})

    _install_client(monkeypatch, handler, rec)
    with pytest.raises(httpx.HTTPStatusError):
        await delete_authentik_account("x@example.com")


# ── 6. seam — get_or_create_stub_user provisions only when the switch is on ─────
from tests.conftest import requires_db  # noqa: E402


@requires_db
@pytest.mark.asyncio
async def test_stub_seam_provisions_when_switch_on(monkeypatch):
    import app.services.invitation_service as inv

    calls: list[tuple[str, str]] = []

    async def spy(email: str, name: str) -> None:
        calls.append((email, name))

    monkeypatch.setattr(inv, "provision_authentik_account", spy)

    email = f"seam-on-{uuid.uuid4().hex[:8]}@example.com"

    # switch ON ⇒ provisioned with the stub email.
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    await inv.get_or_create_stub_user(email, "Seam On")
    assert calls == [(email, "Seam On")]


@requires_db
@pytest.mark.asyncio
async def test_stub_seam_inert_when_not_authentik(monkeypatch):
    import app.services.invitation_service as inv

    calls: list[tuple[str, str]] = []

    async def spy(email: str, name: str) -> None:
        calls.append((email, name))

    monkeypatch.setattr(inv, "provision_authentik_account", spy)
    monkeypatch.setattr(settings, "auth_provider", "disabled")

    email = f"seam-off-{uuid.uuid4().hex[:8]}@example.com"
    await inv.get_or_create_stub_user(email, "Seam Off")
    assert calls == []  # switch off ⇒ not called at all
