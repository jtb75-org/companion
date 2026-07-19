"""Regression: full-transcript persistence survives the mid-request commit.

ROOT CAUSE THIS LOCKS
---------------------
`/conversation/message` (and `/message/stream`) commit the tool-executor
changes mid-request. That commit ends the request transaction, which releases
the transaction-local `app.current_user_id` GUC (see app/db/context.py). The
chat-persistence block that follows then ran a `ChatSession` SELECT in a NEW,
context-less transaction — under the `chat_sessions_isolation` RLS policy that
matches ZERO rows, so `db_session` was None and BOTH the user and assistant
turns were silently dropped (`CHAT_PERSIST_MISSING`). Only `/start` persisted,
because it commits once with the GUC still live — hence every prod session had
`message_count=1`, one assistant row, zero user rows.

The fix re-establishes the same authenticated user's tenant context
(`set_user_context`) after the mid-request commit and BEFORE the persistence
SELECT/INSERTs, so they run inside RLS instead of matching zero rows.

WHY AN ORDERING TEST, NOT AN RLS INTEGRATION TEST
-------------------------------------------------
CI applies the migrations (RLS policies exist) but the app's test engine
connects as the `companion` Postgres superuser, which BYPASSES RLS. So a test
driven through the normal app engine would find the row with or without the GUC
and could NOT reproduce the silent drop. The defect is purely one of ORDERING
inside the endpoint — `set_user_context` must run after the tool-commit and
before the persistence lookup — so we assert exactly that contract with a
recording fake session + a spy on `set_user_context`. This runs hermetically
(no DB, no network) and fails loudly if the re-set is removed or reordered.
"""

from __future__ import annotations

import uuid

import pytest

import app.api.v1.conversation as conv
import app.conversation.safety as safety
from app.conversation.state_manager import ConversationState
from app.schemas.conversation import ConversationMessageRequest

pytestmark = pytest.mark.asyncio


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDBSession:
    """Stand-in for the ORM ChatSession row the persistence SELECT resolves."""

    def __init__(self, user_id):
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.message_count = 1  # the /start greeting already persisted


class _RecordingDB:
    """AsyncSession-shaped fake that records the ORDER of the operations the
    endpoint performs, so the ordering contract can be asserted."""

    def __init__(self, db_session):
        self._db_session = db_session
        self.events: list = []
        self.added: list = []

    async def execute(self, statement, *args, **kwargs):
        self.events.append("execute")
        return _FakeResult(self._db_session)

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")

    def add(self, obj):
        self.events.append("add")
        self.added.append(obj)


class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.voice_id = "warm"


def _install_common_stubs(monkeypatch, db, session, set_ctx_calls):
    """Stub everything the endpoint touches except the persistence path, and
    spy on set_user_context (recording into the SAME event list as the db)."""

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

    async def _set_user_context(_db, user_id):
        # Record into the db's event stream so ordering is comparable, and
        # capture the id to prove it re-sets the SAME authenticated user.
        db.events.append("set_context")
        set_ctx_calls.append(user_id)

    monkeypatch.setattr(conv, "set_user_context", _set_user_context)


async def test_send_message_resets_tenant_context_before_persist(monkeypatch):
    user = _FakeUser()
    db_session = _FakeDBSession(user.id)
    db = _RecordingDB(db_session)
    session = ConversationState(session_id="sess-1", user_id=str(user.id))
    set_ctx_calls: list = []

    _install_common_stubs(monkeypatch, db, session, set_ctx_calls)

    async def _generate_with_tools(*_a, **_k):
        return "assistant reply"

    monkeypatch.setattr(conv, "_generate_with_tools", _generate_with_tools)

    result = await conv.send_message(
        ConversationMessageRequest(text="hello D.D."),
        user=user,
        db=db,
    )

    # set_user_context must be re-set for the SAME user, after the mid-request
    # (tool-executor) commit and BEFORE the persistence SELECT.
    assert set_ctx_calls == [user.id]
    assert "set_context" in db.events
    first_commit = db.events.index("commit")
    set_ctx = db.events.index("set_context")
    select_at = db.events.index("execute")
    assert first_commit < set_ctx < select_at, db.events

    # Both turns (user + assistant) persisted and message_count advanced past 1.
    assert db.events.count("add") == 2
    roles = sorted(m.role for m in db.added)
    assert roles == ["assistant", "user"]
    assert db_session.message_count == 3  # 1 (greeting) + 2
    assert result["response"] == "assistant reply"


async def test_send_message_stream_resets_tenant_context_before_persist(monkeypatch):
    user = _FakeUser()
    db_session = _FakeDBSession(user.id)
    db = _RecordingDB(db_session)
    session = ConversationState(session_id="sess-2", user_id=str(user.id))
    set_ctx_calls: list = []

    _install_common_stubs(monkeypatch, db, session, set_ctx_calls)

    # get_llm_client is already stubbed by the autouse stub_ai_backends fixture
    # to a StubGeminiClient whose generate_stream yields deterministic chunks.
    response = await conv.send_message_stream(
        ConversationMessageRequest(text="hello over stream"),
        user=user,
        db=db,
    )

    # Drive the SSE generator so the persistence block (which runs AFTER the
    # request handler returned) actually executes.
    async for _chunk in response.body_iterator:
        pass

    assert set_ctx_calls == [user.id]
    set_ctx = db.events.index("set_context")
    select_at = db.events.index("execute")
    assert set_ctx < select_at, db.events

    assert db.events.count("add") == 2
    roles = sorted(m.role for m in db.added)
    assert roles == ["assistant", "user"]
    assert db_session.message_count == 3  # 1 (greeting) + 2
