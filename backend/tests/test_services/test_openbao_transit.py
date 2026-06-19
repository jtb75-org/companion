"""Unit tests for OpenBao Transit DEK wrapping (mocked Transit HTTP).

No live OpenBao: a fake httpx.Client stands in for the Transit server,
implementing k8s-auth login + transit/encrypt + transit/decrypt with an
in-memory deterministic "wrap" so DEK roundtrips are exercised end-to-end.

These tests cover:
- wrap produces a ``vault:``-tagged blob and ``transit:<key>`` kek_id;
- unwrap roundtrips; full encrypt_for_user -> decrypt_for_user roundtrip;
- cross-user field decrypt still fails (AAD unchanged);
- fail-closed when Transit raises (no local fallback) outside dev/test;
- token re-login on a 403 then success.
"""

from __future__ import annotations

import base64
import os
import uuid

import httpx
import pytest

from app.config import settings
from app.models.user_encryption_key import UserEncryptionKey
from app.services import field_crypto, openbao_transit


def _gen_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class FakeDB:
    def __init__(self) -> None:
        self._rows: dict = {}

    async def get(self, model, pk):
        if model is UserEncryptionKey:
            return self._rows.get(pk)
        return None

    def add(self, obj) -> None:
        if isinstance(obj, UserEncryptionKey):
            self._rows[obj.user_id] = obj

    async def flush(self) -> None:
        pass


class FakeTransitHTTP:
    """In-memory stand-in for httpx.Client talking to OpenBao.

    - ``auth/kubernetes/login`` -> a client_token (counts logins).
    - ``transit/encrypt/<key>`` -> ``vault:v1:<b64(plaintext_b64)>`` (reversible).
    - ``transit/decrypt/<key>`` -> the original plaintext_b64.

    ``fail_logins_until``/``deny_first_transit`` drive the failure-path tests.
    """

    def __init__(self) -> None:
        self.login_count = 0
        self.encrypt_count = 0
        self.decrypt_count = 0
        # When set, transit calls return 403 until a *fresh* login happens.
        self.deny_until_relogin = False
        self._authorized_token: str | None = None
        # When set, transit/encrypt|decrypt raise a transport error.
        self.raise_transport = False

    def _resp(self, status: int, json_body: dict) -> httpx.Response:
        return httpx.Response(status, json=json_body)

    def post(self, url: str, *, json=None, headers=None) -> httpx.Response:
        if url.endswith("/v1/auth/kubernetes/login"):
            self.login_count += 1
            token = f"s.token-{self.login_count}"
            self._authorized_token = token
            # A fresh login clears any pending denial.
            self.deny_until_relogin = False
            return self._resp(
                200,
                {"auth": {"client_token": token, "lease_duration": 3600}},
            )

        # Transit op.
        if self.raise_transport:
            raise httpx.ConnectError("connection refused")

        token = (headers or {}).get("X-Vault-Token")
        if self.deny_until_relogin or token != self._authorized_token:
            return self._resp(403, {"errors": ["permission denied"]})

        if "/transit/encrypt/" in url:
            self.encrypt_count += 1
            pt = json["plaintext"]
            ct = "vault:v1:" + base64.b64encode(pt.encode()).decode()
            return self._resp(200, {"data": {"ciphertext": ct}})

        if "/transit/decrypt/" in url:
            self.decrypt_count += 1
            ct = json["ciphertext"]
            assert ct.startswith("vault:v1:")
            pt = base64.b64decode(ct[len("vault:v1:"):]).decode()
            return self._resp(200, {"data": {"plaintext": pt}})

        return self._resp(404, {"errors": ["no route"]})


@pytest.fixture
def fake_http() -> FakeTransitHTTP:
    return FakeTransitHTTP()


@pytest.fixture(autouse=True)
def _reset(monkeypatch, fake_http):
    """Configure OpenBao + inject the fake HTTP client; reset all caches."""
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    openbao_transit.reset_client()

    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "field_level_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "openbao_addr", "http://openbao.test:8200")
    monkeypatch.setattr(settings, "openbao_transit_key", "companion-kek")
    monkeypatch.setattr(settings, "openbao_transit_mount", "transit")
    monkeypatch.setattr(settings, "openbao_k8s_role", "companion")
    monkeypatch.setattr(settings, "openbao_k8s_auth_mount", "kubernetes")

    # Build the singleton with the fake HTTP client + a fake SA token path.
    client = openbao_transit.OpenBaoTransitClient(
        addr=settings.openbao_addr,
        transit_key=settings.openbao_transit_key,
        transit_mount=settings.openbao_transit_mount,
        k8s_role=settings.openbao_k8s_role,
        k8s_auth_mount=settings.openbao_k8s_auth_mount,
        http_client=fake_http,
    )
    # Override SA token reading so no real file is needed.
    client._read_sa_jwt = lambda: "fake.sa.jwt"  # type: ignore[method-assign]
    monkeypatch.setattr(openbao_transit, "get_client", lambda: client)

    yield

    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    openbao_transit.reset_client()


# --------------------------------------------------------------------------
# Wrap / unwrap
# --------------------------------------------------------------------------


def test_wrap_dek_produces_transit_blob():
    dek = os.urandom(32)
    uid = uuid.uuid4()
    wrapped, kek_id = field_crypto._wrap_dek(dek, uid)
    assert wrapped.decode("utf-8").startswith("vault:")
    assert kek_id == "transit:companion-kek"


def test_unwrap_dek_roundtrip():
    dek = os.urandom(32)
    uid = uuid.uuid4()
    wrapped, kek_id = field_crypto._wrap_dek(dek, uid)
    field_crypto._dek_cache.clear()  # force a real unwrap, not a cache hit
    assert field_crypto._unwrap_dek(wrapped, kek_id, uid) == dek


async def test_full_encrypt_decrypt_roundtrip():
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "secret PHI")
    assert ct.startswith("f2:")
    row = await db.get(UserEncryptionKey, uid)
    assert bytes(row.wrapped_dek).decode("utf-8").startswith("vault:")
    assert row.kek_id == "transit:companion-kek"
    # Drop the DEK cache so decrypt re-unwraps via Transit.
    field_crypto._dek_cache.clear()
    assert await field_crypto.decrypt_for_user(db, uid, ct) == "secret PHI"


async def test_cross_user_decrypt_still_fails_aad():
    db = FakeDB()
    a, b = uuid.uuid4(), uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, a, "A's data")
    await field_crypto.encrypt_for_user(db, b, "B's data")
    with pytest.raises(RuntimeError):
        await field_crypto.decrypt_for_user(db, b, ct)


# --------------------------------------------------------------------------
# Token caching + re-login
# --------------------------------------------------------------------------


def test_login_cached_across_calls(fake_http):
    field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    # One login covers multiple transit ops.
    assert fake_http.login_count == 1
    assert fake_http.encrypt_count == 2


def test_relogin_on_403(fake_http):
    # Prime a token.
    field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    assert fake_http.login_count == 1
    # Now the server rejects the cached token until a fresh login.
    fake_http.deny_until_relogin = True
    field_crypto._dek_cache.clear()
    # Should transparently re-login and succeed.
    field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    assert fake_http.login_count == 2


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_wrap_fails_closed_when_transit_unreachable(fake_http):
    fake_http.raise_transport = True
    with pytest.raises(RuntimeError):
        field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())


def test_unwrap_fails_closed_when_transit_unreachable(fake_http):
    wrapped, kek_id = field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    field_crypto._dek_cache.clear()
    fake_http.raise_transport = True
    with pytest.raises(RuntimeError):
        field_crypto._unwrap_dek(wrapped, kek_id, uuid.uuid4())


async def test_encrypt_fails_closed_when_transit_unreachable(fake_http):
    fake_http.raise_transport = True
    with pytest.raises(RuntimeError):
        await field_crypto.encrypt_for_user(FakeDB(), uuid.uuid4(), "x")


def test_transit_blob_unwrap_requires_openbao(monkeypatch):
    """A vault:-tagged DEK cannot be unwrapped once OpenBao is unconfigured."""
    wrapped, kek_id = field_crypto._wrap_dek(os.urandom(32), uuid.uuid4())
    field_crypto._dek_cache.clear()
    monkeypatch.setattr(settings, "openbao_addr", "")
    with pytest.raises(RuntimeError):
        field_crypto._unwrap_dek(wrapped, kek_id, uuid.uuid4())


# --------------------------------------------------------------------------
# Local fallback when OpenBao unset (sanity: existing path still works)
# --------------------------------------------------------------------------


async def test_local_path_when_openbao_unset(monkeypatch):
    monkeypatch.setattr(settings, "openbao_addr", "")
    monkeypatch.setattr(settings, "field_encryption_key", _gen_key())
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "local secret")
    row = await db.get(UserEncryptionKey, uid)
    # Local path: kek_id is the keyring id, wrapped blob is raw bytes.
    assert row.kek_id == "k1"
    assert not bytes(row.wrapped_dek).decode("latin-1").startswith("vault:")
    field_crypto._dek_cache.clear()
    assert await field_crypto.decrypt_for_user(db, uid, ct) == "local secret"
