"""Field-level encryption — per-tenant envelope encryption (AES-256-GCM).

This replaces the single-global-key model (see git history / kms_service.py)
with the architecture from caregiver-access-and-privacy §7:

Layers
------
1. **Versioned KEK keyring** (``COMPANION_FIELD_KEYRING``). The keyring holds
   one or more *key-encryption keys* (KEKs). ``primary`` names the KEK used to
   wrap newly-minted DEKs; any KEK id in the ring can still *unwrap* DEKs that
   were sealed under it, so KEKs can be rotated without re-encrypting data. For
   back-compat the legacy ``COMPANION_FIELD_ENCRYPTION_KEY`` is folded in as an
   implicit ``k1`` and is also the key for the legacy single-key (``f1:``) path.

2. **Per-tenant DEK** (``user_encryption_keys`` table). Each user has a random
   32-byte *data-encryption key* (DEK), stored wrapped:
   ``wrapped_dek = AESGCM(KEK[primary]).encrypt(nonce, dek, aad=user_id_bytes)``
   with the wrapping ``kek_id`` recorded alongside. The DEK is created lazily on
   first write for the user.

3. **Field ciphertext** is tagged by scheme:
   - ``f2:<b64(nonce||ct)>`` — envelope: ``ct = AESGCM(dek).encrypt(nonce,
     plaintext, aad=user_id_bytes)``. The blob carries no key id; the DEK is
     resolved via ``user_id`` and the table row carries its own ``kek_id`` for
     unwrap. Binding ``user_id`` as AAD means a field encrypted for user A can
     never be decrypted under user B's DEK.
   - ``f1:<b64(nonce||ct)>`` — legacy single-key path (decrypt-only support;
     uses the keyring's ``k1`` / legacy key).
   - ``enc:<plaintext>`` — dev/test marker (NOT encryption).
   - ``fl1:<b64(nonce||ct)>`` — dedicated field-level-key path for sensitive
     field *types* (per-field-type key, NOT per-user). Capability only today.

Fail-closed: outside development/test a missing keyring raises, an unknown KEK
id raises, and untagged ciphertext raises rather than leaking plaintext.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from collections import OrderedDict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)

# Ciphertext scheme tags.
_ENVELOPE_PREFIX = "f2:"
_LEGACY_PREFIX = "f1:"
_DEV_PREFIX = "enc:"
_FIELD_LEVEL_PREFIX = "fl1:"

_NONCE_BYTES = 12  # 96-bit nonce, recommended for AES-GCM
_DEK_BYTES = 32  # AES-256 data key


def _is_dev() -> bool:
    return settings.environment in ("development", "test")


# ---------------------------------------------------------------------------
# Keyring (KEKs)
# ---------------------------------------------------------------------------


class _Keyring:
    """A versioned set of KEKs: id -> 32 raw bytes, with a primary id."""

    def __init__(self, keys: dict[str, bytes], primary: str | None) -> None:
        self.keys = keys
        self.primary = primary

    def get(self, kek_id: str) -> bytes:
        key = self.keys.get(kek_id)
        if key is None:
            raise RuntimeError(
                f"unknown KEK id {kek_id!r} — cannot unwrap (key rotated out "
                "of the keyring?)"
            )
        return key

    def primary_key(self) -> tuple[str, bytes]:
        if self.primary is None or self.primary not in self.keys:
            raise RuntimeError(
                "field keyring has no usable primary KEK; set "
                "COMPANION_FIELD_KEYRING or COMPANION_FIELD_ENCRYPTION_KEY"
            )
        return self.primary, self.keys[self.primary]


def _decode_key(raw: str, ctx: str) -> bytes:
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"{ctx} must be base64 of 32 bytes (AES-256); got {len(key)} bytes"
        )
    return key


def _build_keyring() -> _Keyring:
    """Build the KEK keyring from config.

    Precedence: ``field_keyring`` JSON if set; otherwise the legacy
    ``field_encryption_key`` as an implicit ``k1`` primary. The legacy key, if
    present, is always also registered as ``k1`` so old ``f1:`` data and DEKs
    wrapped under ``k1`` keep decrypting after a keyring is introduced.
    """
    keys: dict[str, bytes] = {}
    primary: str | None = None

    legacy = settings.field_encryption_key
    if legacy:
        keys["k1"] = _decode_key(legacy, "COMPANION_FIELD_ENCRYPTION_KEY")
        primary = "k1"

    if settings.field_keyring:
        try:
            parsed = json.loads(settings.field_keyring)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "COMPANION_FIELD_KEYRING must be valid JSON "
                '{"primary": "...", "keys": {...}}'
            ) from exc
        ring = parsed.get("keys") or {}
        for kek_id, raw in ring.items():
            keys[kek_id] = _decode_key(raw, f"COMPANION_FIELD_KEYRING[{kek_id}]")
        primary = parsed.get("primary") or primary

    return _Keyring(keys=keys, primary=primary)


# The keyring is derived purely from config (process-wide, immutable per
# deploy). Cache it but allow tests to reset.
_keyring_lock = threading.Lock()
_keyring_cache: _Keyring | None = None


def _keyring() -> _Keyring:
    global _keyring_cache
    with _keyring_lock:
        if _keyring_cache is None:
            _keyring_cache = _build_keyring()
        return _keyring_cache


def reset_keyring_cache() -> None:
    """Drop cached keyrings (tests that patch settings call this)."""
    global _keyring_cache, _field_level_keyring_cache
    with _keyring_lock:
        _keyring_cache = None
        _field_level_keyring_cache = None


# ---------------------------------------------------------------------------
# Per-user DEK (envelope)
# ---------------------------------------------------------------------------

# Cache unwrapped DEKs keyed by the *wrapped* blob (which is opaque and unique
# per user+KEK). This is safe to share across requests: the wrapped blob can
# only have been produced from the configured KEK, and we re-verify the AAD on
# unwrap. We deliberately do NOT key this by user_id alone (that would let a
# stale entry survive a DEK rotation / re-wrap).
#
# Bounded LRU so the process-global cache can't grow without limit (one entry
# per active user's wrapped DEK). Eviction never affects correctness — an
# evicted DEK is simply re-unwrapped from its (cheap) AES-GCM blob on next use.
_DEK_CACHE_MAX = 2048
_dek_lock = threading.Lock()
_dek_cache: OrderedDict[bytes, bytes] = OrderedDict()


def _dek_cache_get(wrapped_blob: bytes) -> bytes | None:
    with _dek_lock:
        dek = _dek_cache.get(wrapped_blob)
        if dek is not None:
            _dek_cache.move_to_end(wrapped_blob)
        return dek


def _dek_cache_put(wrapped_blob: bytes, dek: bytes) -> None:
    with _dek_lock:
        _dek_cache[wrapped_blob] = dek
        _dek_cache.move_to_end(wrapped_blob)
        while len(_dek_cache) > _DEK_CACHE_MAX:
            _dek_cache.popitem(last=False)


def _user_aad(user_id) -> bytes:
    """Stable AAD bytes for a user id (str or uuid.UUID)."""
    return str(user_id).encode("utf-8")


def _wrap_dek(dek: bytes, user_id) -> tuple[bytes, str]:
    kek_id, kek = _keyring().primary_key()
    nonce = os.urandom(_NONCE_BYTES)
    wrapped = AESGCM(kek).encrypt(nonce, dek, _user_aad(user_id))
    return nonce + wrapped, kek_id


def _unwrap_dek(wrapped_blob: bytes, kek_id: str, user_id) -> bytes:
    cached = _dek_cache_get(wrapped_blob)
    if cached is not None:
        return cached
    kek = _keyring().get(kek_id)
    nonce, ct = wrapped_blob[:_NONCE_BYTES], wrapped_blob[_NONCE_BYTES:]
    try:
        dek = AESGCM(kek).decrypt(nonce, ct, _user_aad(user_id))
    except InvalidTag as exc:
        raise RuntimeError(
            "failed to unwrap user DEK (wrong KEK or tampered row)"
        ) from exc
    _dek_cache_put(wrapped_blob, dek)
    return dek


async def _get_or_create_dek(db, user_id) -> bytes:
    """Return the raw DEK for ``user_id``, creating+persisting it if absent."""
    # Imported lazily to avoid import cycles at module load.
    from app.models.user_encryption_key import UserEncryptionKey

    row = await db.get(UserEncryptionKey, user_id)
    if row is not None:
        return _unwrap_dek(bytes(row.wrapped_dek), row.kek_id, user_id)

    if not _can_encrypt():
        # Dev/test without a keyring: no envelope possible.
        raise _no_keyring_error("create a user DEK")

    dek = os.urandom(_DEK_BYTES)
    wrapped, kek_id = _wrap_dek(dek, user_id)
    row = UserEncryptionKey(
        user_id=user_id, wrapped_dek=wrapped, kek_id=kek_id
    )
    db.add(row)
    # Flush so a concurrent/get within the same session sees it; the caller's
    # transaction commits it.
    await db.flush()
    _dek_cache_put(wrapped, dek)
    return dek


def _can_encrypt() -> bool:
    ring = _keyring()
    return ring.primary is not None and ring.primary in ring.keys


def _no_keyring_error(action: str) -> RuntimeError:
    return RuntimeError(
        f"a field encryption keyring is required to {action}; set "
        "COMPANION_FIELD_KEYRING or COMPANION_FIELD_ENCRYPTION_KEY"
    )


# ---------------------------------------------------------------------------
# Public string-level API (envelope)
# ---------------------------------------------------------------------------


async def encrypt_for_user(db, user_id, plaintext: str) -> str:
    """Encrypt ``plaintext`` under ``user_id``'s DEK -> ``f2:...``.

    In development/test with no keyring configured, returns a ``enc:`` dev
    marker so round-trips work without keys. Fails closed otherwise.
    """
    if plaintext is None:  # defensive; callers should guard
        raise ValueError("encrypt_for_user got None plaintext")
    if not _can_encrypt():
        if _is_dev():
            return f"{_DEV_PREFIX}{plaintext}"
        raise _no_keyring_error("encrypt field values")
    dek = await _get_or_create_dek(db, user_id)
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), _user_aad(user_id))
    blob = base64.b64encode(nonce + ct).decode("utf-8")
    return f"{_ENVELOPE_PREFIX}{blob}"


async def decrypt_for_user(db, user_id, ciphertext: str) -> str:
    """Decrypt a tagged field ciphertext for ``user_id``.

    Dispatch: ``f2:`` -> envelope via the user's DEK; ``f1:`` -> legacy
    single-key path; ``enc:`` -> dev marker; ``fl1:`` -> field-level key;
    untagged -> fail closed in prod.
    """
    if ciphertext.startswith(_DEV_PREFIX):
        return ciphertext[len(_DEV_PREFIX) :]

    if ciphertext.startswith(_ENVELOPE_PREFIX):
        from app.models.user_encryption_key import UserEncryptionKey

        row = await db.get(UserEncryptionKey, user_id)
        if row is None:
            raise RuntimeError(
                f"no DEK row for user {user_id} — cannot decrypt f2: value"
            )
        dek = _unwrap_dek(bytes(row.wrapped_dek), row.kek_id, user_id)
        blob = base64.b64decode(ciphertext[len(_ENVELOPE_PREFIX) :])
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            return AESGCM(dek).decrypt(nonce, ct, _user_aad(user_id)).decode("utf-8")
        except InvalidTag as exc:
            raise RuntimeError(
                "field decryption failed (bad DEK/data or wrong user)"
            ) from exc

    if ciphertext.startswith(_LEGACY_PREFIX):
        return _decrypt_legacy(ciphertext)

    if ciphertext.startswith(_FIELD_LEVEL_PREFIX):
        return decrypt_field_level(ciphertext)

    # Untagged / unknown.
    if _is_dev():
        return ciphertext
    raise RuntimeError("refusing to return untagged ciphertext in prod")


def _legacy_key() -> bytes:
    """The legacy single key == keyring 'k1' (or the configured legacy key)."""
    ring = _keyring()
    key = ring.keys.get("k1")
    if key is None:
        raise RuntimeError(
            "no legacy/k1 key available to decrypt f1: values"
        )
    return key


def _decrypt_legacy(ciphertext: str) -> str:
    key = _legacy_key()
    blob = base64.b64decode(ciphertext[len(_LEGACY_PREFIX) :])
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except InvalidTag as exc:
        raise RuntimeError("legacy field decryption failed (bad key/data)") from exc


# ---------------------------------------------------------------------------
# JSON convenience wrappers
# ---------------------------------------------------------------------------


async def encrypt_json_for_user(db, user_id, value) -> str:
    """json.dumps then encrypt. ``None`` passes through as ``None``."""
    if value is None:
        return None  # type: ignore[return-value]
    return await encrypt_for_user(db, user_id, json.dumps(value, default=str))


async def decrypt_json_for_user(db, user_id, ciphertext):
    if ciphertext is None:
        return None
    return json.loads(await decrypt_for_user(db, user_id, ciphertext))


# ---------------------------------------------------------------------------
# Row-field read helpers (mechanical migration of read sites)
# ---------------------------------------------------------------------------

# Which model attributes hold JSON (decode to obj) vs plain text.
_JSON_FIELDS = {
    "extracted_fields",
    "proposed_record_data",
    "value",  # FunctionalMemory.value
    "address",  # User.address
}


async def decrypt_row_field(db, row, attr: str):
    """Decrypt ``getattr(row, attr)`` using ``row.user_id`` (or the row's own
    id for ``User`` rows). Returns ``None`` for ``None``/empty. JSON-typed
    attributes are json-decoded.

    The stored value is plain Text holding a tagged ciphertext. If a raw
    (already-decoded) non-str value somehow appears, it is returned as-is.
    """
    raw = getattr(row, attr, None)
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw

    # Resolve the owning user. Document/PendingReview/FunctionalMemory carry
    # user_id; the User row itself owns its own profile fields.
    user_id = getattr(row, "user_id", None)
    if user_id is None:
        user_id = getattr(row, "id", None)
    if user_id is None:
        raise RuntimeError(
            f"decrypt_row_field: cannot resolve owning user for {row!r}.{attr}"
        )

    if attr in _JSON_FIELDS:
        return await decrypt_json_for_user(db, user_id, raw)
    return await decrypt_for_user(db, user_id, raw)


async def decrypt_value(db, user_id, raw, *, as_json: bool = False):
    """Decrypt a raw column value selected outside the ORM (e.g. a column-only
    ``select``), where ``user_id`` must be supplied explicitly.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    if as_json:
        return await decrypt_json_for_user(db, user_id, raw)
    return await decrypt_for_user(db, user_id, raw)


# ---------------------------------------------------------------------------
# Profile PII helper
# ---------------------------------------------------------------------------


async def set_user_profile_pii(
    db,
    user,
    *,
    phone: str | None = ...,  # type: ignore[assignment]
    date_of_birth=...,
    address=...,
) -> None:
    """Encrypt-and-set sensitive profile fields on a (flushed) ``user`` row.

    Pass a value to set/clear it; omit (leave the sentinel) to leave it
    untouched. ``user`` must already have an ``id`` (flush new rows first) so
    the DEK can be bound to it. ``phone``/``date_of_birth`` are stored as
    encrypted strings; ``address`` as encrypted JSON.

    NOTE for the safety reviewer: only phone/date_of_birth/address are
    encrypted. first_name/last_name/display_name/preferred_name/email are
    intentionally left plaintext (auth gates, display, and unique-email
    lookups depend on them).
    """
    sentinel = ...
    if phone is not sentinel:
        user.phone = (
            await encrypt_for_user(db, user.id, str(phone)) if phone else None
        )
    if date_of_birth is not sentinel:
        user.date_of_birth = (
            await encrypt_for_user(db, user.id, str(date_of_birth))
            if date_of_birth
            else None
        )
    if address is not sentinel:
        user.address = (
            await encrypt_json_for_user(db, user.id, address)
            if address
            else None
        )


async def get_user_phone(db, user) -> str | None:
    return await decrypt_row_field(db, user, "phone")


async def get_user_date_of_birth(db, user) -> str | None:
    return await decrypt_row_field(db, user, "date_of_birth")


async def get_user_address(db, user):
    return await decrypt_row_field(db, user, "address")


# ---------------------------------------------------------------------------
# §7 Dedicated field-level key (per-field-TYPE, NOT per-user). Capability only.
# ---------------------------------------------------------------------------

_field_level_keyring_cache: _Keyring | None = None


def _field_level_keyring() -> _Keyring:
    global _field_level_keyring_cache
    with _keyring_lock:
        if _field_level_keyring_cache is None:
            keys: dict[str, bytes] = {}
            primary: str | None = None
            if settings.field_level_keyring:
                try:
                    parsed = json.loads(settings.field_level_keyring)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "COMPANION_FIELD_LEVEL_KEYRING must be valid JSON"
                    ) from exc
                for kek_id, raw in (parsed.get("keys") or {}).items():
                    keys[kek_id] = _decode_key(
                        raw, f"COMPANION_FIELD_LEVEL_KEYRING[{kek_id}]"
                    )
                primary = parsed.get("primary") or primary
            _field_level_keyring_cache = _Keyring(keys=keys, primary=primary)
        return _field_level_keyring_cache


def encrypt_field_level(plaintext: str) -> str:
    """Encrypt a high-sensitivity field TYPE value under the dedicated
    field-level key -> ``fl1:...``. Not per-user (per spec §7).

    No column uses this yet; it is the ready capability that the CI tripwire
    (tests/test_services/test_field_level_tripwire.py) enforces for any future
    SSN/bank/MRN-style field.
    """
    ring = _field_level_keyring()
    if ring.primary is None or ring.primary not in ring.keys:
        if _is_dev():
            return f"{_DEV_PREFIX}{plaintext}"
        raise RuntimeError(
            "COMPANION_FIELD_LEVEL_KEYRING is required to encrypt field-level "
            "values outside development/test"
        )
    _, key = ring.primary_key()
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = base64.b64encode(nonce + ct).decode("utf-8")
    return f"{_FIELD_LEVEL_PREFIX}{blob}"


def decrypt_field_level(ciphertext: str) -> str:
    if ciphertext.startswith(_DEV_PREFIX):
        return ciphertext[len(_DEV_PREFIX) :]
    if not ciphertext.startswith(_FIELD_LEVEL_PREFIX):
        if _is_dev():
            return ciphertext
        raise RuntimeError("refusing to return untagged field-level value in prod")
    ring = _field_level_keyring()
    # Field-level blob carries no key id; try the primary, then any key.
    blob = base64.b64decode(ciphertext[len(_FIELD_LEVEL_PREFIX) :])
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    candidates = []
    if ring.primary and ring.primary in ring.keys:
        candidates.append(ring.keys[ring.primary])
    candidates.extend(
        v for k, v in ring.keys.items() if k != ring.primary
    )
    for key in candidates:
        try:
            return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
        except InvalidTag:
            continue
    raise RuntimeError("field-level decryption failed (no key matched)")
