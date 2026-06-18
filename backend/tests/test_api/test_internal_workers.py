"""Internal worker endpoints are pipeline-key gated and dispatch the worker.

The worker entrypoints are monkeypatched so these tests never touch the DB,
Redis, or any notification/external service — we only assert the HTTP wiring
and auth gate. (The endpoints import their worker lazily inside the handler,
so we patch the worker module's symbol.)
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.internal import workers as workers_api
from app.config import settings
from app.main import app

# (endpoint path, worker module, entrypoint attr, sentinel result)
_WORKERS = [
    ("/api/internal/workers/escalation-check",
     "app.workers.escalation_check", "run_escalation_check",
     {"users_checked": 0, "total_escalated": 0}),
    ("/api/internal/workers/away-monitor",
     "app.workers.away_monitor", "run_away_monitor",
     {"users_in_extended_away": 0, "alerts_sent": 0}),
    ("/api/internal/workers/retention",
     "app.workers.retention", "run_retention_worker",
     {"junk_purged": 0}),
    ("/api/internal/workers/ttl-purge",
     "app.workers.ttl_purge", "run_ttl_purge",
     {"keys_fixed": 0}),
    ("/api/internal/workers/account-deletion",
     "app.workers.deletion_worker", "run_deletion_worker",
     {"pending": 0, "deleted": 0}),
]

_TEST_KEY = "test-pipeline-key"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _stub_worker(monkeypatch, module: str, attr: str, result):
    async def _fake(*args, **kwargs):
        return result

    monkeypatch.setattr(f"{module}.{attr}", _fake)


@pytest.mark.parametrize(
    "path,module,attr,result", _WORKERS,
    ids=[w[0].rsplit("/", 1)[-1] for w in _WORKERS],
)
async def test_worker_runs_with_pipeline_key(
    monkeypatch, path, module, attr, result
):
    # Force the key check on (test env otherwise no-ops the dependency).
    monkeypatch.setattr(settings, "pipeline_api_key", _TEST_KEY)
    _stub_worker(monkeypatch, module, attr, result)
    async with _client() as ac:
        r = await ac.post(path, headers={"X-Pipeline-Key": _TEST_KEY})
    assert r.status_code == 200
    assert r.json() == result


@pytest.mark.parametrize(
    "path,module,attr,result", _WORKERS,
    ids=[w[0].rsplit("/", 1)[-1] for w in _WORKERS],
)
async def test_worker_rejected_without_pipeline_key(
    monkeypatch, path, module, attr, result
):
    monkeypatch.setattr(settings, "pipeline_api_key", _TEST_KEY)
    # Guard: even if auth wrongly passed, the worker must not run for real.
    _stub_worker(monkeypatch, module, attr, result)
    async with _client() as ac:
        r = await ac.post(path)  # no header
    assert r.status_code == 401


async def test_verify_pipeline_key_module_loaded():
    # Sanity: the dependency wiring is the one we expect to gate the router.
    assert workers_api.verify_pipeline_key in [
        d.dependency for d in workers_api.router.dependencies
    ]
