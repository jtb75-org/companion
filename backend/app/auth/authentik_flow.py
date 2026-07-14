"""Drive Authentik's flow executor server-side (BFF native login, Phase A).

PR #2 of the Firebase->Authentik migration — ADDITIVE AND INERT (only the new
``/auth/login`` endpoint calls this, and that endpoint is gated OFF by default).

The SPA/app posts username+password to our API; this module authenticates the
user against Authentik *without* any redirect or Authentik-hosted UI, then obtains
the OIDC id_token so the resulting subject matches what the redirect/OIDC path
would produce (the provider's ``sub_mode`` is a per-provider hash — so we cannot
shortcut by reading ``/core/users/me``; we must complete OIDC).

Sequence (all server-to-server, in-cluster — no browser, no third-party cookies):

1. Drive ``/api/v3/flows/executor/<auth-flow>/`` stage by stage. The executor uses
   a POST->302->GET pattern, so with ``follow_redirects`` each POST returns the
   next challenge JSON. We handle ``ak-stage-identification`` (uid) and
   ``ak-stage-password`` (password); an authenticator/MFA stage raises
   :class:`MfaRequired` (Phase B). The httpx client holds the Authentik session
   cookie across calls.
2. With that session, GET ``/application/o/authorize/`` (PKCE) — already
   authenticated, so Authentik issues the code without re-prompting — then POST
   ``/application/o/token/`` to get the id_token. Returned for verification by the
   :class:`~app.auth.oidc.OIDCVerifier`.

Only IDs/credentials cross this server-to-server boundary; nothing is persisted
here. The caller verifies the id_token and invite-gates/sessions from its claims.

Ported verbatim from HealthCostClarity.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import ssl
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx


class AuthentikError(Exception):
    """Base for flow-driver errors."""


class AuthenticationFailed(AuthentikError):
    """Bad credentials / access denied — maps to 401."""


class MfaRequired(AuthentikError):
    """The flow reached an authenticator/MFA stage — not handled in Phase A."""


class FlowError(AuthentikError):
    """Unexpected flow shape / Authentik error — maps to 502."""


@dataclass(frozen=True, slots=True)
class TokenResult:
    """Raw OIDC tokens from completing the code flow; caller verifies the id_token."""

    id_token: str
    access_token: str | None


def _pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class AuthentikFlowAuthenticator:
    """Authenticates a user via the flow executor, then completes OIDC to get tokens."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_flow_slug: str,
        client_id: str,
        redirect_uri: str,
        scope: str = "openid profile email",
        verify: bool | str = True,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._flow = auth_flow_slug
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scope = scope
        # httpx TLS verification for the Authentik channel: True = system CAs, or a path
        # to a CA bundle (PEM) for a private/internal CA. Only meaningful when base_url is
        # https; harmless for http (dev). See settings.authentik_ca_bundle_path (gate #2).
        self._verify = verify
        self._ssl_context: ssl.SSLContext | None = None

    def _tls_verify(self) -> bool | ssl.SSLContext:
        """httpx TLS `verify`. A CA bundle PATH is turned into an SSLContext once (httpx
        deprecates passing a str to `verify`); a bool passes through. Built lazily so the
        CA file is read only when a private CA is configured (prod) — never in dev/inert."""
        if isinstance(self._verify, str):
            if self._ssl_context is None:
                self._ssl_context = ssl.create_default_context(cafile=self._verify)
            return self._ssl_context
        return self._verify

    async def authenticate(
        self, username: str, password: str, *, client: httpx.AsyncClient | None = None
    ) -> TokenResult:
        owns = client is None
        client = client or httpx.AsyncClient(
            base_url=self._base,
            follow_redirects=True,
            timeout=10.0,
            verify=self._tls_verify(),
        )
        try:
            await self._run_flow(client, username, password)
            return await self._complete_oidc(client)
        finally:
            if owns:
                await client.aclose()

    # --- step 1: flow executor ---
    async def _run_flow(self, client: httpx.AsyncClient, username: str, password: str) -> None:
        path = f"/api/v3/flows/executor/{self._flow}/?query="
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        challenge = (await client.get(path, headers=headers)).json()
        for _ in range(8):  # bounded — guards against a misconfigured looping flow
            component = challenge.get("component", "")
            # Bad creds re-render the same stage with response_errors — check first,
            # else we'd resubmit the same password forever until the limit.
            if challenge.get("response_errors") or component == "ak-stage-access-denied":
                raise AuthenticationFailed("invalid credentials")
            if component == "ak-stage-identification":
                challenge = (
                    await client.post(
                        path, headers=headers,
                        json={"component": component, "uid_field": username},
                    )
                ).json()
            elif component == "ak-stage-password":
                challenge = (
                    await client.post(
                        path, headers=headers, json={"component": component, "password": password}
                    )
                ).json()
            elif component in ("xak-flow-redirect", "ak-stage-flow-final") or component == "":
                return  # flow complete; Authentik session is now on the client
            elif "authenticator" in component or "mfa" in component:
                raise MfaRequired(f"MFA stage not supported in Phase A: {component}")
            else:
                raise FlowError(f"unexpected flow stage: {component!r}")
        raise FlowError("flow did not complete within the stage limit")

    # --- step 2: complete OIDC with the established session ---
    async def _complete_oidc(self, client: httpx.AsyncClient) -> TokenResult:
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(16)
        authorize = await client.get(
            "/application/o/authorize/",
            params={
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": self._scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        location = authorize.headers.get("location", "")
        if authorize.status_code not in (302, 303) or not location:
            raise FlowError(f"authorize did not redirect (status {authorize.status_code})")
        qs = parse_qs(urlparse(location).query)
        code = qs.get("code", [None])[0]
        if not code:
            # e.g. redirected to a consent flow, or an error param
            raise FlowError(f"no authorization code in redirect: {qs.get('error', location)}")

        token = await client.post(
            "/application/o/token/",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        if token.status_code != 200:
            raise FlowError(f"token exchange failed: {token.status_code} {token.text[:200]}")
        body = token.json()
        if "id_token" not in body:
            raise FlowError("token response missing id_token")
        return TokenResult(id_token=body["id_token"], access_token=body.get("access_token"))
