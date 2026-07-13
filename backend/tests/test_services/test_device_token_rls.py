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


class _FakeNested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


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

    def begin_nested(self):
        return _FakeNested()

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


class _RaceDB:
    """Fake session that raises IntegrityError on the first insert flush (a
    concurrent re-claim of the globally-unique token), then behaves per script.

    ``requery`` is what the post-IntegrityError same-user re-query returns:
    an object → the token became ours (refresh path); None → still not ours
    (release + retry-insert path, which succeeds on attempt 2).
    """

    def __init__(self, requery):
        self._requery = requery
        self.execute_calls = 0
        self.flush_calls = 0
        self.added = []

    async def execute(self, statement):
        self.execute_calls += 1
        # 1st execute = initial same-user lookup (not ours). Later executes =
        # the post-failure re-query.
        return _FakeResult(None if self.execute_calls == 1 else self._requery)

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        return _FakeNested()

    async def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 1:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("insert", {}, Exception("dup fcm_token"))


async def test_race_retry_inserts_on_second_attempt(monkeypatch):
    """IntegrityError on first insert, still-not-ours on re-query → release again
    and the second insert attempt succeeds."""
    releases = []

    async def _fake_release(fcm_token, user_id):
        releases.append(fcm_token)
        return 1

    monkeypatch.setattr(
        device_token_service, "_release_token_other_user", _fake_release
    )
    db = _RaceDB(requery=None)
    tok = await device_token_service.register_token(
        db, uuid4(), "tok-race", "ios", None
    )
    assert tok.fcm_token == "tok-race"
    assert len(releases) == 2  # released before each of the two insert attempts
    assert db.flush_calls == 2  # first raised, second succeeded


async def test_race_retry_refreshes_when_token_became_ours(monkeypatch):
    """IntegrityError on first insert, and the re-query shows the token now
    belongs to us (a concurrent register for this user won) → refresh, no insert."""
    releases = []

    async def _fake_release(fcm_token, user_id):
        releases.append(fcm_token)
        return 1

    monkeypatch.setattr(
        device_token_service, "_release_token_other_user", _fake_release
    )

    class _Ours:
        device_platform = None
        device_name = None
        is_active = False
        last_used_at = None

    ours = _Ours()
    db = _RaceDB(requery=ours)
    tok = await device_token_service.register_token(
        db, uuid4(), "tok-race", "android", "phone"
    )
    assert tok is ours
    assert tok.is_active is True
    assert tok.device_platform == "android"
    assert len(releases) == 1  # only the first release ran; no second insert attempt
    assert db.flush_calls == 2  # first insert flush raised; refresh flush succeeded


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
