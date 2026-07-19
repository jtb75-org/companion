"""publish_pipeline_event is a no-op (Firestore was retired with firebase-admin)."""

from __future__ import annotations

from app.config import settings
from app.pipeline import events


async def test_noop_when_firestore_disabled(monkeypatch):
    """Default (disabled): returns immediately, nothing logged."""
    monkeypatch.setattr(settings, "firestore_pipeline_events", False)
    monkeypatch.setattr(events, "_warned_disabled", False)
    # Must not raise and must return None regardless of args.
    assert await events.publish_pipeline_event("doc-1", "ocr", "done") is None


async def test_noop_and_warns_once_when_enabled(monkeypatch):
    """If the vestigial setting is enabled, we still no-op (Firestore is gone) and warn
    exactly once so logs aren't flooded per stage."""
    monkeypatch.setattr(settings, "firestore_pipeline_events", True)
    monkeypatch.setattr(events, "_warned_disabled", False)

    warnings: list[str] = []
    monkeypatch.setattr(
        events.logger, "warning", lambda msg, *a, **k: warnings.append(msg)
    )

    assert await events.publish_pipeline_event("doc-1", "ocr", "done") is None
    assert await events.publish_pipeline_event("doc-2", "ocr", "done") is None
    # Warned once, not per call.
    assert len(warnings) == 1
