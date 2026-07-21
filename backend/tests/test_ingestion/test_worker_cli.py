"""Hermetic tests for the worker CLI arg-parsing + exit-code contract.

``run_source`` is stubbed so no DB or network is touched — these only pin the
CLI's source/mode resolution and the success→0 / abort→1 exit code.
"""

import pytest

from app.ingestion import worker
from app.ingestion.reconciler import RunSummary
from app.ingestion.types import IngestionMode

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _summary(status: str) -> RunSummary:
    import uuid

    return RunSummary(run_id=uuid.uuid4(), source="eCFR", mode="reconcile", status=status)


def test_default_mode_per_source(monkeypatch):
    """eCFR defaults to full reconcile; fedreg defaults to incremental."""
    seen = {}

    async def fake_run(source, mode):
        seen["source"], seen["mode"] = source, mode
        return _summary("success")

    monkeypatch.setattr(worker, "run", fake_run)

    assert worker.main(["--source", "ecfr"]) == 0
    assert seen["mode"] is IngestionMode.RECONCILE

    assert worker.main(["--source", "fedreg"]) == 0
    assert seen["mode"] is IngestionMode.INCREMENTAL


def test_explicit_mode_override(monkeypatch):
    captured = {}

    async def fake_run(source, mode):
        captured["mode"] = mode
        return _summary("success")

    monkeypatch.setattr(worker, "run", fake_run)
    worker.main(["--source", "fedreg", "--mode", "reconcile"])
    assert captured["mode"] is IngestionMode.RECONCILE


def test_nonsuccess_run_exits_nonzero(monkeypatch):
    async def fake_run(source, mode):
        return _summary("aborted_fetch")

    monkeypatch.setattr(worker, "run", fake_run)
    assert worker.main(["--source", "ecfr"]) == 1


def test_unknown_source_rejected():
    with pytest.raises(SystemExit):
        worker.main(["--source", "westlaw"])  # not in the allowlisted adapter registry
