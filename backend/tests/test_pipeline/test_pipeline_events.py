"""publish_pipeline_event is a no-op when Firestore is disabled (retired)."""

from __future__ import annotations

from unittest.mock import patch

from app.config import settings
from app.pipeline import events


async def test_noop_when_firestore_disabled(monkeypatch):
    monkeypatch.setattr(settings, "firestore_pipeline_events", False)
    with patch.object(events, "_get_firestore") as get_fs:
        await events.publish_pipeline_event("doc-1", "ocr", "done")
        get_fs.assert_not_called()  # never even tries to reach Firestore


async def test_attempts_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "firestore_pipeline_events", True)
    with patch.object(events, "_get_firestore", return_value=None) as get_fs:
        # client unavailable -> returns cleanly, but the gate let it try.
        await events.publish_pipeline_event("doc-1", "ocr", "done")
        get_fs.assert_called_once()
