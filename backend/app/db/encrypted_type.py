"""SQLAlchemy custom type for transparent field-level encryption."""

import json
from typing import Any

from sqlalchemy import Text, TypeDecorator


def _kms():
    """Lazy import to avoid circular dependency."""
    from app.services.kms_service import get_kms_service

    return get_kms_service()


class EncryptedText(TypeDecorator):
    """Transparently encrypts/decrypts text columns (AES-256-GCM)."""

    impl = Text
    cache_ok = False

    def process_bind_param(
        self, value: str | None, dialect: Any
    ) -> str | None:
        if value is None:
            return None
        return _kms().encrypt(value)

    def process_result_value(
        self, value: str | None, dialect: Any
    ) -> str | None:
        if value is None:
            return None
        return _kms().decrypt(value)


class EncryptedJSON(TypeDecorator):
    """Transparently encrypts/decrypts JSON columns (AES-256-GCM)."""

    impl = Text
    cache_ok = False

    def process_bind_param(
        self, value: Any, dialect: Any
    ) -> str | None:
        if value is None:
            return None
        plaintext = json.dumps(value)
        return _kms().encrypt(plaintext)

    def process_result_value(
        self, value: str | None, dialect: Any
    ) -> Any:
        if value is None:
            return None
        plaintext = _kms().decrypt(value)
        return json.loads(plaintext)
