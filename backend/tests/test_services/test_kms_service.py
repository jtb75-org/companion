"""Unit tests for the legacy kms_service shim (decrypt-only).

The live field-encryption logic now lives in services/field_crypto.py (see
test_field_crypto.py). This module only verifies the back-compat shim: it
still decrypts legacy ``f1:``/``enc:`` values, fails closed on untagged data in
prod, and refuses to encrypt (un-tenanted writes are no longer allowed).
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.services import field_crypto, kms_service


def _gen_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _reset():
    field_crypto.reset_keyring_cache()
    yield
    field_crypto.reset_keyring_cache()


def _f1(plaintext: str, key_b64: str) -> str:
    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return "f1:" + base64.b64encode(nonce + ct).decode()


def test_get_kms_service_still_importable():
    assert kms_service.get_kms_service() is kms_service.get_kms_service()


def test_decrypt_legacy_f1(monkeypatch):
    key = _gen_key()
    monkeypatch.setattr(settings, "field_encryption_key", key)
    field_crypto.reset_keyring_cache()
    blob = _f1("legacy PHI", key)
    assert kms_service.get_kms_service().decrypt(blob) == "legacy PHI"


def test_decrypt_dev_marker():
    assert kms_service.get_kms_service().decrypt("enc:hello") == "hello"


def test_encrypt_is_removed():
    with pytest.raises(RuntimeError):
        kms_service.get_kms_service().encrypt("nope")


def test_untagged_fails_closed_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError):
        kms_service.get_kms_service().decrypt("raw-untagged")
