"""Unit tests for the Authentik OIDCVerifier (pure, no network).

We sign RS256 tokens with a locally-generated key and inject a fake PyJWKClient
so the verifier never reaches out for a JWKS. Covers the happy path, expiry, bad
audience, and the require_issuer quirk (BFF in-cluster tokens vs browser bearers).
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.oidc import OIDCVerifier, TokenError

_ISSUER = "https://auth.mydailydignity.com/application/o/companion/"
_AUDIENCE = "companion-client-id"


class _FakeSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _FakeJWKSClient:
    """Stands in for PyJWKClient — returns the one public key we signed with."""

    def __init__(self, public_key) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):  # noqa: ARG002
        return _FakeSigningKey(self._public_key)


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture
def verifier(keypair):
    _, pub = keypair
    return OIDCVerifier(
        issuer=_ISSUER,
        jwks_uri="https://auth.example/jwks",
        audience=_AUDIENCE,
        jwks_client=_FakeJWKSClient(pub),
    )


def _make_token(priv, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "authentik-per-provider-hash",
        "email": "member@example.com",
        "name": "Test Member",
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, priv, algorithm="RS256")


def test_valid_token_returns_claims(keypair, verifier):
    priv, _ = keypair
    token = _make_token(priv)
    result = verifier.verify(token)
    assert result.sub == "authentik-per-provider-hash"
    assert result.email == "member@example.com"
    assert result.name == "Test Member"


def test_email_verified_claim_exposed(keypair, verifier):
    """The verifier surfaces the OIDC email_verified claim (cutover gate #5). Absent
    or falsey → False; explicit true → True."""
    priv, _ = keypair
    assert verifier.verify(_make_token(priv)).email_verified is False
    assert verifier.verify(_make_token(priv, email_verified=True)).email_verified is True
    assert verifier.verify(_make_token(priv, email_verified=False)).email_verified is False
    # Strict: a non-boolean claim (e.g. the string "false", which is truthy in
    # Python) must NOT be read as verified — `is True` fail-closes (niru #6).
    assert verifier.verify(_make_token(priv, email_verified="false")).email_verified is False
    assert verifier.verify(_make_token(priv, email_verified="true")).email_verified is False
    assert verifier.verify(_make_token(priv, email_verified=1)).email_verified is False


def test_expired_token_rejected(keypair, verifier):
    priv, _ = keypair
    now = int(time.time())
    token = _make_token(priv, iat=now - 600, exp=now - 300)
    with pytest.raises(TokenError):
        verifier.verify(token)


def test_bad_audience_rejected(keypair, verifier):
    priv, _ = keypair
    token = _make_token(priv, aud="some-other-client")
    with pytest.raises(TokenError):
        verifier.verify(token)


def test_wrong_issuer_rejected_when_required(keypair, verifier):
    priv, _ = keypair
    token = _make_token(priv, iss="https://evil.example/")
    with pytest.raises(TokenError):
        verifier.verify(token, require_issuer=True)


def test_internal_issuer_accepted_when_issuer_not_required(keypair, verifier):
    """BFF in-cluster path: issuer_mode=per_provider stamps the internal host as
    iss, so require_issuer=False must accept it (signature+aud still verified)."""
    priv, _ = keypair
    token = _make_token(priv, iss="http://companion-authentik-server.companion-authentik.svc")
    result = verifier.verify(token, require_issuer=False)
    assert result.sub == "authentik-per-provider-hash"


def test_missing_required_claim_rejected(keypair, verifier):
    """The verifier requires exp/iat/sub; a token without sub is rejected."""
    priv, _ = keypair
    now = int(time.time())
    token = jwt.encode(
        {"email": "x@example.com", "aud": _AUDIENCE, "iss": _ISSUER, "iat": now, "exp": now + 300},
        priv,
        algorithm="RS256",
    )
    with pytest.raises(TokenError):
        verifier.verify(token)


def test_constructor_requires_config(keypair):
    _, pub = keypair
    with pytest.raises(RuntimeError):
        OIDCVerifier(issuer="", jwks_uri="", audience="", jwks_client=_FakeJWKSClient(pub))
