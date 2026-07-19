"""Unit tests for services/field_crypto.py — per-tenant envelope encryption.

These don't need a real database: a tiny in-memory fake stands in for the
async session's ``get``/``add``/``flush`` so the full crypto path (DEK
get-or-create, wrap/unwrap, AAD binding, KEK rotation) is exercised.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid

import pytest

from app.config import settings
from app.models.user_encryption_key import UserEncryptionKey
from app.services import field_crypto


def _gen_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class FakeDB:
    """Minimal async-session stand-in for the user_encryption_keys table."""

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


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset keyring + DEK caches and force a clean keyring per test."""
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    # Default: a single-key keyring (legacy k1) in a "prod" env so fail-closed
    # paths are real; individual tests override as needed.
    monkeypatch.setattr(settings, "field_encryption_key", _gen_key())
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "field_level_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    yield
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()


# --------------------------------------------------------------------------
# Envelope (per-user) roundtrip + AAD binding
# --------------------------------------------------------------------------


async def test_f2_roundtrip_per_user():
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "secret PHI")
    assert ct.startswith("f2:")
    assert "secret PHI" not in ct
    assert await field_crypto.decrypt_for_user(db, uid, ct) == "secret PHI"


async def test_f2_nondeterministic():
    db = FakeDB()
    uid = uuid.uuid4()
    a = await field_crypto.encrypt_for_user(db, uid, "x")
    b = await field_crypto.encrypt_for_user(db, uid, "x")
    assert a != b


async def test_dek_created_lazily_once():
    db = FakeDB()
    uid = uuid.uuid4()
    assert await db.get(UserEncryptionKey, uid) is None
    await field_crypto.encrypt_for_user(db, uid, "a")
    row = await db.get(UserEncryptionKey, uid)
    assert row is not None
    wrapped_before = row.wrapped_dek
    # Second write must reuse the same wrapped DEK, not mint a new one.
    await field_crypto.encrypt_for_user(db, uid, "b")
    assert (await db.get(UserEncryptionKey, uid)).wrapped_dek == wrapped_before


async def test_cross_user_decrypt_fails_aad():
    """A field encrypted for user A must not decrypt under user B's DEK."""
    db = FakeDB()
    a, b = uuid.uuid4(), uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, a, "A's data")
    # Give B their own DEK.
    await field_crypto.encrypt_for_user(db, b, "B's data")
    with pytest.raises(RuntimeError):
        await field_crypto.decrypt_for_user(db, b, ct)


async def test_decrypt_f2_without_dek_row_fails():
    db = FakeDB()
    uid = uuid.uuid4()
    # A well-formed f2 blob but no DEK row present for this user.
    with pytest.raises(RuntimeError):
        blob = base64.b64encode(os.urandom(40)).decode()
        await field_crypto.decrypt_for_user(db, uid, "f2:" + blob)


async def test_tampered_f2_fails():
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "hello")
    tampered = ct[:-2] + ("AA" if ct[-2:] != "AA" else "BB")
    with pytest.raises(RuntimeError):
        await field_crypto.decrypt_for_user(db, uid, tampered)


async def test_json_roundtrip():
    db = FakeDB()
    uid = uuid.uuid4()
    obj = {"amount_due": "100.00", "nested": {"a": [1, 2, 3]}}
    ct = await field_crypto.encrypt_json_for_user(db, uid, obj)
    assert ct.startswith("f2:")
    assert await field_crypto.decrypt_json_for_user(db, uid, ct) == obj
    # None passes through.
    assert await field_crypto.encrypt_json_for_user(db, uid, None) is None
    assert await field_crypto.decrypt_json_for_user(db, uid, None) is None


# --------------------------------------------------------------------------
# Per-user keyed content fingerprint (exact-duplicate detection)
# --------------------------------------------------------------------------


async def test_fingerprint_same_user_same_bytes_is_stable():
    """Same member + same bytes -> identical fingerprint (exact-dedup fires)."""
    db = FakeDB()
    uid = uuid.uuid4()
    data = b"\xff\xd8\xff\xe0scanned-page-bytes" * 10
    a = await field_crypto.fingerprint_for_user(db, uid, data)
    b = await field_crypto.fingerprint_for_user(db, uid, data)
    assert a == b
    # Hex HMAC-SHA-256 -> 64 hex chars, and it is NOT the bare SHA-256.
    assert len(a) == 64
    assert a != hashlib.sha256(data).hexdigest()


async def test_fingerprint_different_users_do_not_correlate():
    """Same bytes under two members -> different fingerprints (no cross-member
    correlation; a DB breach can't link identical documents across members)."""
    db = FakeDB()
    a, b = uuid.uuid4(), uuid.uuid4()
    data = b"IDENTICAL-DOCUMENT-BYTES" * 20
    fa = await field_crypto.fingerprint_for_user(db, a, data)
    fb = await field_crypto.fingerprint_for_user(db, b, data)
    assert fa != fb


async def test_fingerprint_different_bytes_same_user_differ():
    db = FakeDB()
    uid = uuid.uuid4()
    fa = await field_crypto.fingerprint_for_user(db, uid, b"page-one")
    fb = await field_crypto.fingerprint_for_user(db, uid, b"page-two")
    assert fa != fb


async def test_fingerprint_dedup_lookup_finds_same_user_duplicate():
    """The exact-dedup lookup key: a re-upload by the SAME member recomputes the
    SAME fingerprint and so matches the stored value, while another member's
    fingerprint of the same bytes does not."""
    db = FakeDB()
    uid, other = uuid.uuid4(), uuid.uuid4()
    data = b"\x89PNG\r\n" + b"BILL" * 100
    stored = await field_crypto.fingerprint_for_user(db, uid, data)
    # Same member re-uploads identical bytes -> matches (dedup hits).
    assert await field_crypto.fingerprint_for_user(db, uid, data) == stored
    # A different member uploading identical bytes -> no match (stays distinct).
    assert await field_crypto.fingerprint_for_user(db, other, data) != stored


async def test_fingerprint_dev_fallback_deterministic_and_per_user(monkeypatch):
    """No keyring in dev/test: still deterministic per (user, bytes) and still
    per-user (so hermetic dedup works without KMS)."""
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "test")
    field_crypto.reset_keyring_cache()
    db = FakeDB()
    a, b = uuid.uuid4(), uuid.uuid4()
    data = b"dev-bytes" * 30
    fa1 = await field_crypto.fingerprint_for_user(db, a, data)
    fa2 = await field_crypto.fingerprint_for_user(db, a, data)
    fb = await field_crypto.fingerprint_for_user(db, b, data)
    assert fa1 == fa2  # deterministic for a member
    assert fa1 != fb  # still uncorrelatable across members


async def test_fingerprint_fails_closed_without_keyring_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    with pytest.raises(RuntimeError):
        await field_crypto.fingerprint_for_user(FakeDB(), uuid.uuid4(), b"x")


# --------------------------------------------------------------------------
# Legacy f1 decrypt
# --------------------------------------------------------------------------


async def test_f1_legacy_decrypt(monkeypatch):
    """f1: ciphertext (legacy single-key) still decrypts via keyring k1."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_b64 = _gen_key()
    monkeypatch.setattr(settings, "field_encryption_key", key_b64)
    field_crypto.reset_keyring_cache()
    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, b"legacy value", None)
    blob = "f1:" + base64.b64encode(nonce + ct).decode()
    db = FakeDB()
    assert await field_crypto.decrypt_for_user(db, uuid.uuid4(), blob) == "legacy value"


# --------------------------------------------------------------------------
# KEK rotation
# --------------------------------------------------------------------------


async def test_kek_rotation(monkeypatch):
    """Rotating the primary KEK: existing DEKs still unwrap, new writes use
    the new primary."""
    k1, k2 = _gen_key(), _gen_key()
    # Phase 1: only k1, primary=k1.
    keyring = {"primary": "k1", "keys": {"k1": k1}}
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", json.dumps(keyring))
    field_crypto.reset_keyring_cache()

    db = FakeDB()
    uid = uuid.uuid4()
    ct_old = await field_crypto.encrypt_for_user(db, uid, "before rotation")
    row = await db.get(UserEncryptionKey, uid)
    assert row.kek_id == "k1"

    # Phase 2: add k2 and make it primary.
    keyring2 = {"primary": "k2", "keys": {"k1": k1, "k2": k2}}
    monkeypatch.setattr(settings, "field_keyring", json.dumps(keyring2))
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()

    # Existing DEK (wrapped under k1) still unwraps -> old ciphertext decrypts.
    assert await field_crypto.decrypt_for_user(db, uid, ct_old) == "before rotation"

    # A brand-new user's DEK is wrapped under the new primary k2.
    uid2 = uuid.uuid4()
    await field_crypto.encrypt_for_user(db, uid2, "x")
    assert (await db.get(UserEncryptionKey, uid2)).kek_id == "k2"


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


async def test_encrypt_fails_closed_without_keyring(monkeypatch):
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    with pytest.raises(RuntimeError):
        await field_crypto.encrypt_for_user(FakeDB(), uuid.uuid4(), "x")


async def test_untagged_fails_closed_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError):
        await field_crypto.decrypt_for_user(FakeDB(), uuid.uuid4(), "raw-untagged")


async def test_empty_is_not_untagged_ciphertext(monkeypatch):
    """An empty value carries no ciphertext — it must pass through (return "")
    in prod, NOT trip the untagged-ciphertext guard. Absent optional fields
    (e.g. a document with no OCR text) legitimately decrypt "" on read."""
    monkeypatch.setattr(settings, "environment", "production")
    db, uid = FakeDB(), uuid.uuid4()
    assert await field_crypto.decrypt_for_user(db, uid, "") == ""
    # decrypt_value: None -> None, "" -> "".
    assert await field_crypto.decrypt_value(db, uid, None) is None
    assert await field_crypto.decrypt_value(db, uid, "") == ""
    # An empty JSON field -> None, not a json.loads("") crash.
    assert await field_crypto.decrypt_json_for_user(db, uid, "") is None


async def test_dev_marker_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "test")
    field_crypto.reset_keyring_cache()
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "hello")
    assert ct == "enc:hello"
    assert await field_crypto.decrypt_for_user(db, uid, ct) == "hello"


async def test_bad_key_length_raises(monkeypatch):
    monkeypatch.setattr(
        settings, "field_encryption_key", base64.b64encode(os.urandom(16)).decode()
    )
    monkeypatch.setattr(settings, "field_keyring", "")
    field_crypto.reset_keyring_cache()
    with pytest.raises(RuntimeError):
        await field_crypto.encrypt_for_user(FakeDB(), uuid.uuid4(), "x")


# --------------------------------------------------------------------------
# DEK cache is a bounded LRU
# --------------------------------------------------------------------------


async def test_dek_cache_is_bounded(monkeypatch):
    """The process-global DEK cache never exceeds its cap, and eviction does
    not break decryption (DEKs are simply re-unwrapped on demand)."""
    monkeypatch.setattr(field_crypto, "_DEK_CACHE_MAX", 4)
    field_crypto._dek_cache.clear()
    db = FakeDB()
    pairs = []
    for _ in range(20):
        uid = uuid.uuid4()
        ct = await field_crypto.encrypt_for_user(db, uid, "phi")
        pairs.append((uid, ct))

    assert len(field_crypto._dek_cache) <= 4

    # Every value still decrypts correctly despite cache eviction.
    for uid, ct in pairs:
        assert await field_crypto.decrypt_for_user(db, uid, ct) == "phi"
    assert len(field_crypto._dek_cache) <= 4


# --------------------------------------------------------------------------
# decrypt_row_field helper
# --------------------------------------------------------------------------


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def test_decrypt_row_field_uses_user_id():
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "card text")
    row = _Row(user_id=uid, card_summary=ct)
    assert await field_crypto.decrypt_row_field(db, row, "card_summary") == "card text"


async def test_decrypt_row_field_json_attr():
    db = FakeDB()
    uid = uuid.uuid4()
    obj = {"k": "v"}
    ct = await field_crypto.encrypt_json_for_user(db, uid, obj)
    row = _Row(user_id=uid, proposed_record_data=ct)
    assert await field_crypto.decrypt_row_field(db, row, "proposed_record_data") == obj


async def test_decrypt_row_field_none():
    row = _Row(user_id=uuid.uuid4(), card_summary=None)
    assert await field_crypto.decrypt_row_field(FakeDB(), row, "card_summary") is None


async def test_decrypt_row_field_user_owns_self():
    """For a User row (no user_id attr) the row's own id is the owner."""
    db = FakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "555-1212")
    user_row = _Row(id=uid, phone=ct)
    assert await field_crypto.decrypt_row_field(db, user_row, "phone") == "555-1212"


# --------------------------------------------------------------------------
# Profile PII helper
# --------------------------------------------------------------------------


async def test_set_user_profile_pii_roundtrip():
    db = FakeDB()
    uid = uuid.uuid4()
    user = _Row(id=uid, phone=None, date_of_birth=None, address=None)
    await field_crypto.set_user_profile_pii(
        db, user, phone="555-0000", date_of_birth="1950-01-02",
        address={"city": "Springfield"},
    )
    assert user.phone.startswith("f2:")
    assert user.date_of_birth.startswith("f2:")
    assert user.address.startswith("f2:")
    assert await field_crypto.get_user_phone(db, user) == "555-0000"
    assert await field_crypto.get_user_date_of_birth(db, user) == "1950-01-02"
    assert await field_crypto.get_user_address(db, user) == {"city": "Springfield"}


async def test_set_user_profile_pii_partial_and_clear():
    db = FakeDB()
    uid = uuid.uuid4()
    user = _Row(id=uid, phone="f2:existing", date_of_birth=None, address=None)
    # Omitting phone leaves it untouched; clearing dob/address sets None.
    await field_crypto.set_user_profile_pii(db, user, date_of_birth=None, address=None)
    assert user.phone == "f2:existing"
    assert user.date_of_birth is None
    assert user.address is None


# --------------------------------------------------------------------------
# §7 dedicated field-level key
# --------------------------------------------------------------------------


async def test_field_level_roundtrip(monkeypatch):
    ring = {"primary": "fl1", "keys": {"fl1": _gen_key()}}
    monkeypatch.setattr(settings, "field_level_keyring", json.dumps(ring))
    field_crypto.reset_keyring_cache()
    ct = field_crypto.encrypt_field_level("123-45-6789")
    assert ct.startswith("fl1:")
    assert "123-45-6789" not in ct
    assert field_crypto.decrypt_field_level(ct) == "123-45-6789"


async def test_field_level_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "field_level_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    with pytest.raises(RuntimeError):
        field_crypto.encrypt_field_level("x")
