"""OIDC token verification (companion-authentik).

PR #2 of the Firebase->Authentik migration. This module is ADDITIVE AND INERT:
nothing in the running app calls it yet (Firebase remains the live verifier for
every existing endpoint). It exists so the BFF native-login endpoint
(``app/api/auth_authentik.py``) can verify the id_token it obtains from Authentik.

Ported verbatim from HealthCostClarity's proven pattern (only the JWKS User-Agent
differs). Verifies an RS256 OIDC JWT against a cached JWKS plus issuer/audience/
expiry, and exposes the claims we key off (``sub``, email, name). Authorization is
always ours (invite-only gate + RLS), never Authentik's group claims.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    sub: str
    email: str | None
    name: str | None
    claims: dict
    # OIDC ``email_verified`` claim. Defaults to False when absent so a caller that
    # trusts ``email`` for identity binding must see an explicit true assertion (the
    # invite-only / backfill binding relies on this — see auth_authentik.login).
    email_verified: bool = False


class TokenError(Exception):
    """Token missing / malformed / invalid signature / expired — maps to 401."""


class OIDCVerifier:
    """Verifies RS256 OIDC JWTs against a cached JWKS. The signing-key client is
    injectable so tests can supply a local key without network access."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks_uri: str,
        audience: str,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        if not (issuer and jwks_uri and audience):
            raise RuntimeError("OIDC issuer / jwks_uri / audience must be configured")
        self._issuer = issuer
        self._audience = audience
        # Explicit User-Agent: the public JWKS URL is fronted by Cloudflare, whose
        # bot WAF 403s the default "Python-urllib/*" UA (any other UA passes).
        self._jwks = jwks_client or PyJWKClient(
            jwks_uri, cache_keys=True, headers={"User-Agent": "companion-api"}
        )

    def verify(self, token: str, *, require_issuer: bool = True) -> VerifiedToken:
        """Verify signature (JWKS) + audience + expiry, and by default the issuer.

        ``require_issuer=False`` is for tokens the BFF obtained itself directly from
        Authentik's token endpoint over the trusted in-cluster channel: there
        Authentik stamps ``iss`` with the *internal* host (issuer_mode=per_provider),
        which won't equal the public issuer — but the signature already proves it
        came from this Authentik, so the iss host is redundant. Bearer tokens from
        the browser MUST keep require_issuer=True (untrusted source)."""
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer if require_issuer else None,
                options={"require": ["exp", "iat", "sub"], "verify_iss": require_issuer},
            )
        except Exception as exc:  # PyJWKClientError, InvalidTokenError, ...
            raise TokenError(str(exc)) from exc
        return VerifiedToken(
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name") or claims.get("preferred_username"),
            claims=claims,
            email_verified=bool(claims.get("email_verified")),
        )
