"""Field-level encryption — local AES-256-GCM with a symmetric key.

Replaces Cloud KMS for the self-hosted deployment, per migration-plan
§Phase 9 and caregiver-access-and-privacy §7 (AES-256-GCM). The key
(``COMPANION_FIELD_ENCRYPTION_KEY``, base64 of 32 random bytes —
``openssl rand -base64 32``) is delivered via a SealedSecret.

Ciphertext is tagged ``f1:`` and stored as ``f1:<base64(nonce||ct)>`` so
the scheme/key can be versioned and rotated later (e.g. a ``f2:`` with
key-id'd MultiFernet-style rotation, per-tenant keys — see follow-ups).

Kept the ``get_kms_service`` name and encrypt/decrypt interface so the
SQLAlchemy ``EncryptedText``/``EncryptedJSON`` types need no changes.
"""

import base64
import logging
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)

_VERSION_PREFIX = "f1:"
_DEV_PREFIX = "enc:"  # legacy/local-dev marker (NOT encryption)
_NONCE_BYTES = 12  # 96-bit nonce, recommended for AES-GCM


@lru_cache
def _key() -> bytes | None:
    """Decode the configured key to 32 raw bytes, or None if unset."""
    raw = settings.field_encryption_key
    if not raw:
        return None
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            "COMPANION_FIELD_ENCRYPTION_KEY must be base64 of 32 bytes "
            f"(AES-256); got {len(key)} bytes"
        )
    return key


def _require_key(action: str) -> bytes:
    key = _key()
    if key is None:
        raise RuntimeError(
            f"COMPANION_FIELD_ENCRYPTION_KEY is required to {action}"
        )
    return key


class LocalEncryptionService:
    """Symmetric field encryption backed by a local AES-256-GCM key."""

    def encrypt(self, plaintext: str) -> str:
        if _key() is None:
            # No key: only tolerated in dev/test, where we store a marked
            # plaintext so round-trips work without a key.
            if settings.environment in ("development", "test"):
                return f"{_DEV_PREFIX}{plaintext}"
            raise RuntimeError(
                "COMPANION_FIELD_ENCRYPTION_KEY is required outside "
                "development/test"
            )
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
        blob = base64.b64encode(nonce + ct).decode("utf-8")
        return f"{_VERSION_PREFIX}{blob}"

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext.startswith(_DEV_PREFIX):
            return ciphertext[len(_DEV_PREFIX) :]
        if ciphertext.startswith(_VERSION_PREFIX):
            key = _require_key("decrypt f1: values")
            blob = base64.b64decode(ciphertext[len(_VERSION_PREFIX) :])
            nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
            try:
                return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
            except InvalidTag as exc:
                raise RuntimeError("field decryption failed (bad key/data)") from exc
        # Unknown/untagged value. No such data exists today; fail closed
        # outside dev/test rather than returning raw bytes.
        if settings.environment in ("development", "test"):
            return ciphertext
        raise RuntimeError("refusing to return untagged ciphertext in prod")


@lru_cache
def get_kms_service() -> LocalEncryptionService:
    return LocalEncryptionService()
