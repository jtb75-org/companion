"""Regression test for the document-pipeline RLS GUC re-set (WS1 Phase 2f).

The Pub/Sub handler sets the tenant GUC, runs the pipeline, and commits — the
commit ends the transaction and clears the transaction-local GUC. The follow-up
notify reads the member's device_tokens (an RLS tenant table), so the handler
must RE-SET the tenant context after that commit or the token lookup fail-closes
to zero and the push is silently dropped. This locks that ordering.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.api.pipeline import document_handler


async def test_guc_reset_between_commit_and_notify(monkeypatch):
    events: list = []
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

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

    class _FakeDB:
        async def commit(self):
            events.append("commit")

        async def rollback(self):
            events.append("rollback")

    payload = {
        "payload": {"document_id": str(document_id)},
        "user_id": str(user_id),
    }
    result = await document_handler.handle_document_received_push(payload, _FakeDB())

    assert result["status"] == "processed"
    # The tenant context must be set before the pipeline AND again after the
    # first commit (which cleared it) before notify reads device_tokens.
    assert events == [
        ("set_ctx", user_id),
        "process",
        "commit",
        ("set_ctx", user_id),
        ("notify", user_id, "a bill summary"),
        "commit",
    ]
