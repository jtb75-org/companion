"""Legacy field-encryption shim — kept for back-compat.

The field-encryption architecture now lives in :mod:`app.services.field_crypto`
(per-tenant envelope encryption with a versioned KEK keyring). This module is
retained only so that:

* ``get_kms_service()`` keeps working for any caller that still imports it, and
* the legacy single-key (``f1:``) and dev (``enc:``) paths remain decryptable.

The legacy key is the keyring's ``k1`` (folded in from
``COMPANION_FIELD_ENCRYPTION_KEY``). New code should call
``field_crypto.encrypt_for_user`` / ``decrypt_for_user`` instead. There is no
non-user-scoped *encrypt* path anymore — ``LocalEncryptionService.encrypt``
raises to prevent accidentally writing un-tenanted ciphertext.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.services import field_crypto

logger = logging.getLogger(__name__)


class LocalEncryptionService:
    """Decrypt-only shim over the legacy single-key path."""

    def encrypt(self, plaintext: str) -> str:  # pragma: no cover - guard
        raise RuntimeError(
            "LocalEncryptionService.encrypt is removed; use "
            "field_crypto.encrypt_for_user(db, user_id, plaintext)"
        )

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext.startswith(field_crypto._DEV_PREFIX):
            return ciphertext[len(field_crypto._DEV_PREFIX) :]
        if ciphertext.startswith(field_crypto._LEGACY_PREFIX):
            return field_crypto._decrypt_legacy(ciphertext)
        if settings.environment in ("development", "test"):
            return ciphertext
        raise RuntimeError("refusing to return untagged ciphertext in prod")


@lru_cache
def get_kms_service() -> LocalEncryptionService:
    return LocalEncryptionService()
