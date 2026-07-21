"""Authentik cutover-hardening gates: #3 (client-IP / XFF trust) + #6 (CORS).

#3 affects the login throttle bucket and #6 the browser SPA CSRF-header preflight; we
lock the behavior in so the Authentik login surface stays safe.
"""

from __future__ import annotations

import pytest
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


# ── #6: CORS must allow the BFF CSRF header ──


def test_cors_config_allows_csrf_header():
    """The CORS middleware is configured to allow X-CSRF-Token (env-independent —
    the source of truth regardless of which origins a given environment permits)."""
    from app.main import app

    cors = next(
        m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert "X-CSRF-Token" in cors.kwargs["allow_headers"]


async def test_cors_preflight_allows_csrf_header():
    """Behavioral: a browser SPA's preflight for an unsafe session request carries
    Access-Control-Request-Headers: X-CSRF-Token; it must be allowed. Uses the app's
    OWN configured origins so it isn't tied to one environment (skips if none)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import _cors_origins, app

    if not _cors_origins:
        pytest.skip("no CORS origins configured in this environment")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        r = await ac.options(
            "/api/v1/me",
            headers={
                "Origin": _cors_origins[0],
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-CSRF-Token",
            },
        )
    assert r.status_code in (200, 204)
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allowed


def test_cors_never_combines_credentials_with_wildcard_or_regex():
    """Guard the CSRF-token-in-body invariant (PR #124, safety follow-up). The double-
    submit token now rides in the /auth/check + /auth/login response bodies, so its
    confidentiality — and that of the email/role fields — depends on CORS staying a
    STRICT static allowlist. A future regression to a wildcard origin, or an
    allow_origin_regex combined with credentials, would turn /auth/check into a
    cross-origin token-exfiltration endpoint. Assert that can never ship."""
    from app.main import CORS_ORIGINS, app

    cors = next(
        m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    if cors.kwargs.get("allow_credentials"):
        assert "*" not in cors.kwargs.get("allow_origins", []), (
            "credentialed CORS must never use a wildcard origin"
        )
        assert not cors.kwargs.get("allow_origin_regex"), (
            "credentialed CORS must not use allow_origin_regex (reflected-origin risk)"
        )
    # No configured environment may list a wildcard origin.
    for env, origins in CORS_ORIGINS.items():
        assert "*" not in origins, f"CORS_ORIGINS[{env!r}] must not contain '*'"


def test_cors_prod_allows_www_landing_origin():
    """The public benefits-helper widget on the www landing makes a credentialed
    cross-origin POST to api.mydailydignity.com/public/knowledge/ask, so www must be an
    allowed prod origin. Exact-origin match only — a look-alike is still rejected."""
    from app.main import CORS_ORIGINS

    prod = CORS_ORIGINS["prod"]
    assert "https://www.mydailydignity.com" in prod
    # Exact-origin allowlist: a non-listed / look-alike origin must NOT be permitted.
    assert "https://evil.mydailydignity.com.attacker.com" not in prod
    assert "https://www.mydailydignity.com.evil.com" not in prod


# ── gate #2: TLS to Authentik — CA bundle threads config → authenticator → httpx ──


def test_authenticator_uses_ca_bundle_when_configured(monkeypatch):
    """A configured CA bundle path is threaded to the flow authenticator so httpx
    verifies Authentik's TLS against the internal CA (cutover gate #2)."""
    from app.api.auth_authentik import _authenticator

    monkeypatch.setattr(settings, "authentik_ca_bundle_path", "/etc/authentik-ca/ca.crt")
    assert _authenticator()._verify == "/etc/authentik-ca/ca.crt"


def test_authenticator_defaults_to_system_cas(monkeypatch):
    """Empty CA bundle → verify=True (httpx system CAs); the dev default."""
    from app.api.auth_authentik import _authenticator

    monkeypatch.setattr(settings, "authentik_ca_bundle_path", "")
    assert _authenticator()._verify is True


def _self_signed_ca_pem() -> bytes:
    """A throwaway self-signed CA PEM, so ssl.create_default_context(cafile=...) loads."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


async def test_flow_client_uses_ssl_context_for_ca_bundle(monkeypatch, tmp_path):
    """A configured CA bundle PATH is turned into an ssl.SSLContext for the httpx client
    (httpx deprecates verify=<str>), and that context is actually applied — not dropped."""
    import contextlib
    import ssl

    import httpx

    from app.auth.authentik_flow import AuthentikFlowAuthenticator

    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(_self_signed_ca_pem())

    captured: dict = {}
    real_client = httpx.AsyncClient

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _spy)
    auth = AuthentikFlowAuthenticator(
        base_url="https://authentik.internal",
        auth_flow_slug="f",
        client_id="c",
        redirect_uri="r",
        verify=str(ca_file),
    )
    # It will fail to connect (no server), but the client is constructed first.
    with contextlib.suppress(Exception):
        await auth.authenticate("u", "p")
    assert isinstance(captured.get("verify"), ssl.SSLContext)


def test_tls_verify_passes_bool_through():
    """verify=True (system CAs) passes through unchanged — no SSLContext, no file read."""
    from app.auth.authentik_flow import AuthentikFlowAuthenticator

    auth = AuthentikFlowAuthenticator(
        base_url="https://x", auth_flow_slug="f", client_id="c", redirect_uri="r"
    )
    assert auth._tls_verify() is True
