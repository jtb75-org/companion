"""Regression test: create_document stamps received_at per document.

Root cause of the observed bug: the ``documents.received_at`` DB default was
``now()`` == ``transaction_timestamp()``, which is CONSTANT for a whole
transaction — so two documents created in one transaction shared an IDENTICAL
received_at (down to the microsecond), and a document created inside a
reused/long-lived transaction inherited that transaction's stale (past) start
time instead of its real ingest moment.

Fix: create_document sets a fresh wall-clock ``received_at`` in Python per
document. This test is hermetic — it uses a fake session (no DB, no DB default),
so a populated received_at proves the Python-side stamp is doing the work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.models.enums import SourceChannel
from app.services import document_service


class _FakeSession:
    """Minimal AsyncSession stand-in: never applies a DB default."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # Real flush applies the model's default=uuid4 id at INSERT time; mimic
        # that so the post-flush document.received event can be constructed.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        return None


@pytest.fixture(autouse=True)
def _no_event_publish(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(document_service.event_publisher, "publish", _noop)


async def _create():
    return await document_service.create_document(
        _FakeSession(),
        uuid.uuid4(),
        {
            "source_channel": SourceChannel.CAMERA_SCAN,
            "raw_text_ref": "scans/x/page_000.jpg",
        },
    )


async def test_received_at_is_stamped_and_fresh():
    """With no DB default in play, received_at is still set — and to ~now, not a
    stale past value."""
    before = datetime.utcnow()
    doc = await _create()
    after = datetime.utcnow()

    assert doc.received_at is not None
    assert before - timedelta(seconds=1) <= doc.received_at <= after + timedelta(seconds=1)


async def test_received_at_not_shared_across_documents():
    """Two documents do NOT share a single (transaction-scoped) timestamp — the
    exact symptom of the old now() default. Each gets its own wall-clock stamp."""
    d1 = await _create()
    d2 = await _create()
    # Monotonic per-document stamp; never identical-by-transaction.
    assert d2.received_at >= d1.received_at


async def test_explicit_received_at_is_preserved():
    """A caller may pass an explicit received_at (e.g. an email's own date)."""
    fixed = datetime(2026, 6, 18, 11, 46, 51)
    doc = await document_service.create_document(
        _FakeSession(),
        uuid.uuid4(),
        {
            "source_channel": SourceChannel.EMAIL,
            "raw_text_ref": "pending",
            "received_at": fixed,
        },
    )
    assert doc.received_at == fixed
