"""Authentik cutover-hardening gates: #3 (client-IP / XFF trust) + #6 (CORS).

Both are inert on the live Firebase path — #3 only affects the (404-guarded) login
throttle bucket, and #6 only matters once a browser SPA sends the CSRF header — but we
lock the behavior in so the cutover flip is safe.
"""

from __future__ import annotations

from starlette.requests import Request

from app.api.auth_authentik import _client_ip
from app.config import settings


def _req(headers: dict | None = None, client=("9.9.9.9", 0)) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "client": client,
        }
    )


# ── #3: login-throttle client IP must not be spoofable via X-Forwarded-For ──


def test_client_ip_prefers_cf_connecting_ip():
    """cf-connecting-ip (Cloudflare, unspoofable via the tunnel) wins over XFF."""
    ip = _client_ip(
        _req({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"})
    )
    assert ip == "1.1.1.1"


def test_client_ip_ignores_xff_by_default(monkeypatch):
    """With no cf header and trust_forwarded_for=False (default), a client-injectable
    XFF must NOT be trusted — fall back to the direct peer so it can't poison the
    throttle."""
    monkeypatch.setattr(settings, "trust_forwarded_for", False)
    ip = _client_ip(_req({"x-forwarded-for": "6.6.6.6"}, client=("9.9.9.9", 0)))
    assert ip == "9.9.9.9"


def test_client_ip_honors_xff_only_when_trusted(monkeypatch):
    """A deployment whose trusted proxy owns XFF opts in explicitly."""
    monkeypatch.setattr(settings, "trust_forwarded_for", True)
    ip = _client_ip(_req({"x-forwarded-for": "6.6.6.6, 7.7.7.7"}))
    assert ip == "6.6.6.6"


def test_client_ip_peer_fallback():
    assert _client_ip(_req({}, client=("3.3.3.3", 0))) == "3.3.3.3"
    assert _client_ip(_req({}, client=None)) == "unknown"


# ── #6: CORS preflight must allow the BFF CSRF header ──


async def test_cors_preflight_allows_csrf_header():
    """A browser SPA's preflight for an unsafe session request carries
    Access-Control-Request-Headers: X-CSRF-Token. It must be allowed, or the request
    never reaches the app."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.options(
            "/api/v1/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-CSRF-Token",
            },
        )
    assert r.status_code in (200, 204)
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allowed
