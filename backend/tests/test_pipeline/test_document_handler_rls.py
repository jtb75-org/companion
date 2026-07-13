"""Regression tests for the document-pipeline trigger + RLS GUC handling.

- run_document_pipeline must set the tenant GUC, run the pipeline, commit, then
  RE-SET the GUC (the commit clears the transaction-local GUC) before the notify
  reads device_tokens (an RLS table) — else the push fail-closes to zero tokens.
- The document.received local subscriber (the post-Pub/Sub trigger) must parse
  the envelope and dispatch run_document_pipeline in the background.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.api.pipeline import document_handler
from app.events import subscribers


class _FakeSession:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self._events.append("commit")

    async def rollback(self):
        self._events.append("rollback")


async def test_guc_reset_between_commit_and_notify(monkeypatch):
    events: list = []
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

    monkeypatch.setattr(
        document_handler, "async_session_factory", lambda: _FakeSession(events)
    )

    async def fake_set_ctx(db, uid):
        events.append(("set_ctx", uid))

    async def fake_process(db, doc_id, uid):
        events.append("process")
        return SimpleNamespace(
            summarization=SimpleNamespace(card_summary="a bill summary")
        )

    async def fake_notify(db, uid, summary):
        events.append(("notify", uid, summary))

    monkeypatch.setattr(document_handler, "set_user_context", fake_set_ctx)
    monkeypatch.setattr(document_handler, "process_document", fake_process)
    monkeypatch.setattr(document_handler, "notify_document_processed", fake_notify)

    await document_handler.run_document_pipeline(document_id, user_id)

    # Tenant context set before the pipeline AND again after the first commit
    # (which cleared it) before notify reads device_tokens.
    assert events == [
        ("set_ctx", user_id),
        "process",
        "commit",
        ("set_ctx", user_id),
        ("notify", user_id, "a bill summary"),
        "commit",
    ]


async def test_document_received_subscriber_dispatches_pipeline(monkeypatch):
    """The local document.received handler parses the envelope and runs the
    pipeline in the background (the trigger that replaced Pub/Sub)."""
    called: list = []
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

    async def fake_run(doc_id, uid):
        called.append((doc_id, uid))

    monkeypatch.setattr(document_handler, "run_document_pipeline", fake_run)

    envelope = {
        "event_name": "document.received",
        "user_id": str(user_id),
        "payload": {"document_id": str(document_id)},
    }
    await subscribers.handle_document_received(envelope)
    # The handler spawns a background task; let it run.
    await asyncio.sleep(0)
    for _ in range(5):
        if called:
            break
        await asyncio.sleep(0)

    assert called == [(document_id, user_id)]


async def test_document_received_subscriber_ignores_bad_envelope(monkeypatch):
    called: list = []

    async def fake_run(doc_id, uid):
        called.append((doc_id, uid))

    monkeypatch.setattr(document_handler, "run_document_pipeline", fake_run)

    # Missing document_id → logged and ignored, no task spawned.
    await subscribers.handle_document_received({"user_id": str(uuid.uuid4()), "payload": {}})
    await asyncio.sleep(0)
    assert called == []
