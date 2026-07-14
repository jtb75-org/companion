"""Provision Authentik user accounts at Companion account-creation seams.

PR 1 of the Authentik provisioning effort — Companion owns provisioning via the
Authentik ADMIN API (branded BFF model, not Authentik-hosted enrollment). This
PR CREATES the account only (no password); a later PR adds the branded
set-password activation so invited/onboarded people can authenticate.

Contract (all three properties are load-bearing — see the seam call sites):

* INERT: a no-op with ZERO HTTP unless BOTH the master switch selects Authentik
  (``settings.auth_provider == "authentik"``) AND an admin API token is set. On
  the Firebase default (or an unset token) this function does nothing.
* IDEMPOTENT: an account matched by email is left untouched, so a retry / re-invite
  transparently heals a prior transient failure.
* BEST-EFFORT: any failure (Authentik down, non-2xx, unexpected shape) is logged
  and swallowed — this NEVER raises, so provisioning can neither 500 nor roll back
  the Companion account row it follows.
"""

from __future__ import annotations

import logging
import ssl

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_ssl_context: ssl.SSLContext | None = None


def _tls_verify() -> bool | ssl.SSLContext:
    """httpx ``verify`` for the Authentik admin channel.

    A configured CA bundle PATH is turned into a cached ``ssl.SSLContext`` once
    (httpx deprecates passing a str to ``verify``); with no CA path we verify
    against the system CAs (``True``). Built lazily so the CA file is read only
    when a private CA is configured (prod) — never in dev/inert. Mirrors
    ``app/auth/authentik_flow.py::_tls_verify``.
    """
    ca_path = settings.authentik_ca_bundle_path
    if ca_path:
        global _ssl_context
        if _ssl_context is None:
            _ssl_context = ssl.create_default_context(cafile=ca_path)
        return _ssl_context
    return True


async def provision_authentik_account(email: str, name: str) -> None:
    """Best-effort, idempotent creation of an Authentik user for ``email``.

    Inert unless Authentik is the selected provider AND an admin API token is
    configured (see module docstring). Never raises.
    """
    # Gate: Firebase default (or no admin token) ⇒ no-op, zero HTTP.
    if not settings.authentik_enabled or not settings.authentik_api_token:
        return

    try:
        async with httpx.AsyncClient(
            base_url=settings.authentik_internal_url,
            verify=_tls_verify(),
            timeout=10.0,
            headers={"Authorization": f"Bearer {settings.authentik_api_token}"},
        ) as client:
            # Idempotent: an existing account (matched by unique email) is a no-op.
            found = await client.get("/api/v3/core/users/", params={"email": email})
            found.raise_for_status()
            if found.json().get("results"):
                log.debug("Authentik account already exists for %s", email)
                return

            # Username == email (emails are unique; avoids a separate username scheme).
            created = await client.post(
                "/api/v3/core/users/",
                json={
                    "username": email,
                    "email": email,
                    "name": name,
                    "type": "internal",
                    "is_active": True,
                    "path": "users",
                },
            )
            created.raise_for_status()
            log.info("provisioned Authentik account for %s", email)
    except Exception:
        # Provisioning must never block or roll back the Companion account it
        # follows — log loudly and continue; a re-invite retries (idempotent).
        log.error("failed to provision Authentik account for %s", email, exc_info=True)
        return
