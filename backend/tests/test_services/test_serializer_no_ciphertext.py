"""Serializers must decrypt per-tenant fields — never emit ``f2:`` ciphertext.

Regression guard for the safety review that BLOCKED the per-tenant encryption
change: several read paths returned raw ORM rows, leaking ``f2:``-tagged
ciphertext to clients. These tests exercise the serializers with a real
envelope-encrypted value and assert no ciphertext escapes.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest

from app.api.v1 import users as users_api
from app.config import settings
from app.models.user_encryption_key import UserEncryptionKey
from app.services import field_crypto, section_service


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


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture(autouse=True)
def _real_keyring(monkeypatch):
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    monkeypatch.setattr(settings, "field_encryption_key", _gen_key())
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    yield
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()


def _assert_no_ciphertext(value) -> None:
    """Recursively assert nothing looks like a tagged ciphertext blob."""
    if isinstance(value, str):
        assert not value.startswith(("f2:", "f1:", "fl1:")), value
    elif isinstance(value, dict):
        for v in value.values():
            _assert_no_ciphertext(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _assert_no_ciphertext(v)


async def test_serialize_document_decrypts_phi():
    db = FakeDB()
    uid = uuid.uuid4()
    doc = _Obj(
        id=uuid.uuid4(),
        user_id=uid,
        source_channel="upload",
        classification="medical",
        confidence_score=0.9,
        urgency_level="urgent",
        extracted_fields=await field_crypto.encrypt_json_for_user(
            db, uid, {"diagnosis": "secret"}
        ),
        spoken_summary=await field_crypto.encrypt_for_user(db, uid, "spoken phi"),
        card_summary=await field_crypto.encrypt_for_user(db, uid, "card phi"),
        routing_destination="health",
        page_count=1,
        status="processed",
        received_at=None,
        processed_at=None,
        acknowledged_at=None,
    )
    out = await section_service._serialize_document(db, doc)
    assert out["extracted_fields"] == {"diagnosis": "secret"}
    assert out["spoken_summary"] == "spoken phi"
    assert out["card_summary"] == "card phi"
    _assert_no_ciphertext(out)


async def test_serialize_user_decrypts_pii():
    db = FakeDB()
    uid = uuid.uuid4()
    user = _Obj(
        id=uid,
        email="x@example.com",
        first_name="Jane",
        last_name="Doe",
        preferred_name="Jane",
        display_name="Jane Doe",
        nickname=None,
        phone=await field_crypto.encrypt_for_user(db, uid, "555-0000"),
        date_of_birth=await field_crypto.encrypt_for_user(db, uid, "1950-01-02"),
        address=await field_crypto.encrypt_json_for_user(db, uid, {"city": "X"}),
        primary_language="en",
        voice_id="warm",
        pace_setting="normal",
        warmth_level="warm",
        quiet_start=None,
        quiet_end=None,
        checkin_time=None,
        away_mode=False,
        account_status="active",
        care_model="independent",
        created_at=None,
    )
    out = await users_api._serialize_user(db, user)
    assert out["phone"] == "555-0000"
    assert out["date_of_birth"] == "1950-01-02"
    assert out["address"] == {"city": "X"}
    _assert_no_ciphertext(out)
