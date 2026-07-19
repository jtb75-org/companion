"""Pre-PHI gate: chat_messages.content is field-encrypted at rest.

Locks the parity guarantee — D.D. transcript turns are persisted under the
SAME per-user AES-256-GCM envelope (``f2:``, user_id bound as AAD, DEK wrapped
by a KEK) as RAG chunk_text / OCR text / document extracted_fields, and decrypt
back on the read paths.

These run hermetically: a real (local) keyring in a "production" env so the
fail-closed envelope path is exercised for real, plus an in-memory fake session
standing in for both the ``user_encryption_keys`` table and the persistence
SELECT (no DB, no network — the AI backends are stubbed by the autouse
``stub_ai_backends`` fixture).
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone

import pytest

import app.api.admin.conversations as admin_conv
import app.api.v1.conversation as conv
import app.conversation.safety as safety
from app.config import settings
from app.conversation.state_manager import ConversationState
from app.models.chat_session import ChatMessage
from app.models.user_encryption_key import UserEncryptionKey
from app.schemas.conversation import ConversationMessageRequest
from app.services import field_crypto

pytestmark = pytest.mark.asyncio


@pytest.fixture
def keyring(monkeypatch):
    """A real local KEK in a prod env so ``f2:`` envelope encryption is live
    and the fail-closed decrypt guard is the real one."""
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "field_encryption_key", key)
    monkeypatch.setattr(settings, "field_keyring", "")
    monkeypatch.setattr(settings, "environment", "production")
    field_crypto.reset_keyring_cache()
    yield
    field_crypto.reset_keyring_cache()
    field_crypto._dek_cache.clear()


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _CryptoFakeDB:
    """AsyncSession-shaped fake backing BOTH the user_encryption_keys table
    (get/add/flush for DEK get-or-create) and the persistence SELECT."""

    def __init__(self, db_session=None):
        self._db_session = db_session
        self._dek_rows: dict = {}
        self.added_messages: list[ChatMessage] = []

    async def get(self, model, pk):
        if model is UserEncryptionKey:
            return self._dek_rows.get(pk)
        return None

    async def execute(self, *_a, **_k):
        return _FakeResult(self._db_session)

    def add(self, obj):
        if isinstance(obj, UserEncryptionKey):
            self._dek_rows[obj.user_id] = obj
        elif isinstance(obj, ChatMessage):
            self.added_messages.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _FakeDBSession:
    def __init__(self, user_id):
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.message_count = 1


class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.voice_id = "warm"


def _install_stubs(monkeypatch, session):
    async def _get_active_session(_uid):
        return session

    async def _update_session(_session):
        return None

    monkeypatch.setattr(conv.state_manager, "get_active_session", _get_active_session)
    monkeypatch.setattr(conv.state_manager, "update_session", _update_session)

    async def _build_system_prompt(*_a, **_k):
        return "system prompt"

    monkeypatch.setattr(conv, "build_system_prompt", _build_system_prompt)

    async def _get_context_window(*_a, **_k):
        return 20

    monkeypatch.setattr(conv, "_get_context_window", _get_context_window)

    def _check_integrity(*_a, **_k):
        return {"alerts": []}

    monkeypatch.setattr(safety, "check_conversation_integrity", _check_integrity)

    async def _handle_exploitation(_text, _uid, prompt, _db):
        return prompt

    monkeypatch.setattr(safety, "handle_exploitation_detection", _handle_exploitation)

    async def _set_user_context(_db, _user_id):
        return None

    monkeypatch.setattr(conv, "set_user_context", _set_user_context)


# --------------------------------------------------------------------------
# Write paths — content persisted encrypted, decrypts back
# --------------------------------------------------------------------------


async def test_send_message_persists_encrypted_content(keyring, monkeypatch):
    user = _FakeUser()
    db = _CryptoFakeDB(_FakeDBSession(user.id))
    session = ConversationState(session_id="sess-1", user_id=str(user.id))
    _install_stubs(monkeypatch, session)

    async def _generate_with_tools(*_a, **_k):
        return "the assistant reply about your bill"

    monkeypatch.setattr(conv, "_generate_with_tools", _generate_with_tools)

    user_text = "my secret PHI diagnosis"
    result = await conv.send_message(
        ConversationMessageRequest(text=user_text), user=user, db=db
    )
    assert result["response"] == "the assistant reply about your bill"

    # Both turns persisted, both stored as f2: ciphertext (no plaintext leak).
    assert len(db.added_messages) == 2
    by_role = {m.role: m.content for m in db.added_messages}
    assert set(by_role) == {"user", "assistant"}
    for role, ct in by_role.items():
        assert ct.startswith("f2:"), (role, ct)
        assert "secret PHI" not in ct
        assert "assistant reply" not in ct

    # ...and decrypt back correctly under the member's DEK.
    assert await field_crypto.decrypt_for_user(db, user.id, by_role["user"]) == user_text
    assert (
        await field_crypto.decrypt_for_user(db, user.id, by_role["assistant"])
        == "the assistant reply about your bill"
    )


async def test_send_message_stream_persists_encrypted_content(keyring, monkeypatch):
    user = _FakeUser()
    db = _CryptoFakeDB(_FakeDBSession(user.id))
    session = ConversationState(session_id="sess-2", user_id=str(user.id))
    _install_stubs(monkeypatch, session)

    response = await conv.send_message_stream(
        ConversationMessageRequest(text="streamed secret symptom"),
        user=user,
        db=db,
    )
    # Drive the SSE generator so the persistence block runs.
    async for _chunk in response.body_iterator:
        pass

    assert len(db.added_messages) == 2
    by_role = {m.role: m.content for m in db.added_messages}
    for role, ct in by_role.items():
        assert ct.startswith("f2:"), (role, ct)
        assert "secret symptom" not in ct
    assert (
        await field_crypto.decrypt_for_user(db, user.id, by_role["user"])
        == "streamed secret symptom"
    )


# --------------------------------------------------------------------------
# Admin read path — returns plaintext to the admin
# --------------------------------------------------------------------------


class _FakeMsg:
    def __init__(self, user_id, role, content):
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.role = role
        self.content = content
        self.created_at = datetime.now(timezone.utc)


class _FakeChatSession:
    def __init__(self, user_id, messages):
        self.id = uuid.uuid4()
        self.session_id = "sess-admin"
        self.user_id = user_id
        self.started_at = datetime.now(timezone.utc)
        self.ended_at = None
        self.message_count = len(messages)
        self.summary = None
        self.messages = messages


async def test_admin_get_conversation_returns_plaintext(keyring):
    db = _CryptoFakeDB()
    uid = uuid.uuid4()
    ct_user = await field_crypto.encrypt_for_user(db, uid, "member: I feel dizzy")
    ct_asst = await field_crypto.encrypt_for_user(db, uid, "D.D.: let's check that")
    db._db_session = _FakeChatSession(
        uid,
        [_FakeMsg(uid, "user", ct_user), _FakeMsg(uid, "assistant", ct_asst)],
    )

    result = await admin_conv.get_conversation("sess-admin", admin=None, db=db)

    contents = [m["content"] for m in result["messages"]]
    assert "member: I feel dizzy" in contents
    assert "D.D.: let's check that" in contents
    # No ciphertext returned to the caller.
    assert not any(c.startswith("f2:") for c in contents)


async def test_admin_export_returns_plaintext(keyring):
    db = _CryptoFakeDB()
    uid = uuid.uuid4()
    ct = await field_crypto.encrypt_for_user(db, uid, "exported private turn")
    session = _FakeChatSession(uid, [_FakeMsg(uid, "user", ct)])

    class _ScalarsResult:
        def scalars(self):
            return self

        def all(self):
            return [session]

    async def _execute(*_a, **_k):
        return _ScalarsResult()

    db.execute = _execute  # type: ignore[assignment]

    result = await admin_conv.export_conversations(admin=None, db=db)
    msg = result["conversations"][0]["messages"][0]
    assert msg["content"] == "exported private turn"


# --------------------------------------------------------------------------
# Cross-user decrypt fails (AAD binding) — fail-closed
# --------------------------------------------------------------------------


async def test_cross_user_decrypt_fails_aad(keyring):
    db = _CryptoFakeDB()
    a, b = uuid.uuid4(), uuid.uuid4()
    ct_a = await field_crypto.encrypt_for_user(db, a, "A's private transcript")
    # Give B their own DEK so the failure is AAD, not a missing key row.
    await field_crypto.encrypt_for_user(db, b, "B's data")
    with pytest.raises(RuntimeError):
        await field_crypto.decrypt_for_user(db, b, ct_a)
