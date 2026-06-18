"""Unit tests for services/kms_service.py — local AES-256-GCM field encryption."""

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import pytest

from app.config import settings
from app.services import kms_service


@pytest.fixture(autouse=True)
def _clear_key_cache():
    """_key() is lru_cached on the configured key; clear around each test."""
    kms_service._key.cache_clear()
    yield
    kms_service._key.cache_clear()


def _gen_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _svc():
    return kms_service.LocalEncryptionService()


def test_aesgcm_roundtrip_with_key():
    with patch.object(settings, "field_encryption_key", _gen_key()):
        svc = _svc()
        ct = svc.encrypt("secret PHI value")
        assert ct.startswith("f1:")
        assert "secret PHI value" not in ct  # actually encrypted
        assert svc.decrypt(ct) == "secret PHI value"


def test_ciphertext_is_nondeterministic():
    """Random nonce per call: same plaintext -> different ciphertext."""
    with patch.object(settings, "field_encryption_key", _gen_key()):
        svc = _svc()
        assert svc.encrypt("x") != svc.encrypt("x")


def test_tampered_ciphertext_fails():
    """GCM auth tag must reject modified ciphertext."""
    with patch.object(settings, "field_encryption_key", _gen_key()):
        svc = _svc()
        ct = svc.encrypt("hello")
        tampered = ct[:-2] + ("AA" if ct[-2:] != "AA" else "BB")
        with pytest.raises(RuntimeError):
            svc.decrypt(tampered)


def test_wrong_key_cannot_decrypt():
    with patch.object(settings, "field_encryption_key", _gen_key()):
        ct = _svc().encrypt("hello")
    kms_service._key.cache_clear()
    with patch.object(settings, "field_encryption_key", _gen_key()):
        with pytest.raises(RuntimeError):
            _svc().decrypt(ct)


def test_bad_key_length_raises():
    with patch.object(
        settings, "field_encryption_key", base64.b64encode(os.urandom(16)).decode()
    ):
        with pytest.raises(RuntimeError):
            _svc().encrypt("x")


def test_dev_fallback_without_key():
    with (
        patch.object(settings, "field_encryption_key", ""),
        patch.object(settings, "environment", "test"),
    ):
        svc = _svc()
        ct = svc.encrypt("hello")
        assert ct == "enc:hello"
        assert svc.decrypt(ct) == "hello"


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_no_key_fails_closed_outside_dev(env):
    with (
        patch.object(settings, "field_encryption_key", ""),
        patch.object(settings, "environment", env),
    ):
        with pytest.raises(RuntimeError):
            _svc().encrypt("x")


def test_untagged_value_fails_closed_in_prod():
    with (
        patch.object(settings, "field_encryption_key", _gen_key()),
        patch.object(settings, "environment", "prod"),
    ):
        with pytest.raises(RuntimeError):
            _svc().decrypt("raw-untagged-value")


def test_decrypt_legacy_enc_prefix_with_key_present():
    """Legacy enc: dev values still decrypt even when a real key is set."""
    with patch.object(settings, "field_encryption_key", _gen_key()):
        assert _svc().decrypt("enc:legacy") == "legacy"
