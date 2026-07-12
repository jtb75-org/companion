"""Tests for the RLS-safe register_token flow (WS1 Phase 2e).

fcm_token is globally unique and can move between users; under per-user RLS the
member session can't see another user's row, so register_token must (a) resolve
the same-user case with a user-scoped lookup, and (b) release a foreign-owned
token via the scoped maintenance helper BEFORE inserting. Pure-unit, no DB.
"""

from __future__ import annotations

from uuid import uuid4

from app.services import device_token_service


class _FakeResult:
    def __init__(self, obj=None, rowcount=0):
        self._obj = obj
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, existing=None):
        self.executed: list[str] = []
        self.added = []
        self._existing = existing

    async def execute(self, statement):
        self.executed.append(str(statement))
        return _FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


async def test_same_user_lookup_is_user_scoped(monkeypatch):
    """The member-session lookup must filter by user_id (RLS-visible rows only),
    never a bare global fcm_token lookup."""
    released = []

    async def _fake_release(fcm_token, user_id):
        released.append(fcm_token)
        return 0

    monkeypatch.setattr(
        device_token_service, "_release_token_other_user", _fake_release
    )
    db = _FakeDB(existing=None)
    await device_token_service.register_token(
        db, uuid4(), "tok-abc", "ios", "Joe's phone"
    )
    select_sql = db.executed[0]
    assert "user_id" in select_sql and "fcm_token" in select_sql

    # Not ours → the cross-tenant release helper must run before the insert.
    assert released == ["tok-abc"]
    assert len(db.added) == 1
    assert db.added[0].fcm_token == "tok-abc"


async def test_same_user_refresh_skips_release(monkeypatch):
    """When the token already belongs to this user, no cross-tenant release and
    no new row — just a refresh."""
    released = []

    async def _fake_release(fcm_token, user_id):
        released.append(fcm_token)
        return 0

    monkeypatch.setattr(
        device_token_service, "_release_token_other_user", _fake_release
    )

    class _Existing:
        device_platform = None
        device_name = None
        is_active = False
        last_used_at = None

    db = _FakeDB(existing=_Existing())
    tok = await device_token_service.register_token(
        db, uuid4(), "tok-abc", "android", None
    )
    assert released == []  # no bypass touched
    assert db.added == []  # no new row
    assert tok.is_active is True
    assert tok.device_platform == "android"
