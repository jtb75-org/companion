"""Tests for the RLS tenant-context GUC helper + auth-dep wiring (WS1 Phase 2).

The GUCs must be set with set_config(..., is_local => true) — transaction-local,
so they never leak across pooled connections — and the auth dependencies must set
them so that when RLS policies land, every request carries its tenant context.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.auth import dependencies as deps
from app.db import context


class _FakeResult:
    def __init__(self, obj=None):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    """Records (sql, params) for every execute; returns a preset user for the
    SELECT so the dev-bypass path can resolve a mock user."""

    def __init__(self, user=None):
        self.calls: list[tuple[str, dict]] = []
        self._user = user

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _FakeResult(self._user)


async def test_set_user_context_is_transaction_local():
    db = _FakeDB()
    uid = uuid.uuid4()
    await context.set_user_context(db, uid)
    sql, params = db.calls[-1]
    assert "set_config('app.current_user_id'" in sql
    assert "true" in sql  # is_local => true (transaction-scoped, no pooled bleed)
    assert params["v"] == str(uid)


async def test_set_login_email_context():
    db = _FakeDB()
    await context.set_login_email_context(db, "a@b.io")
    sql, params = db.calls[-1]
    assert "set_config('app.current_login_email'" in sql
    assert "true" in sql
    assert params["v"] == "a@b.io"


async def test_clear_user_context_sets_empty():
    db = _FakeDB()
    await context.clear_user_context(db)
    _, params = db.calls[-1]
    assert params["v"] == ""  # empty → RLS predicate false → fail-closed


async def test_get_current_user_sets_tenant_context(monkeypatch):
    # Dev-bypass path resolves a mock user; it must set the tenant GUC.
    monkeypatch.setattr(deps.settings, "dev_auth_bypass", True)
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid, account_status="active")
    db = _FakeDB(user=user)

    result = await deps.get_current_user(authorization=None, db=db)
    assert result is user
    # A set_config for app.current_user_id with this user's id must have run.
    assert any(
        "set_config('app.current_user_id'" in sql and params.get("v") == str(uid)
        for sql, params in db.calls
    ), db.calls
