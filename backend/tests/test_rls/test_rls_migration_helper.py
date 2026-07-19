"""Proves ``app.db.rls_migration.rls_bypassed`` actually performs DML on a
FORCE-ROW-LEVEL-SECURITY table when run by a NON-BYPASS *owner* role.

WHY THIS HARNESS IS DIFFERENT FROM test_rls_isolation
-----------------------------------------------------
CI connects as the ``companion`` superuser, which BYPASSES RLS — a naive test
would never reproduce the silent-no-op bug (the superuser deletes the rows
regardless). It also cannot reuse ``test_rls_isolation``'s ``rlstest_app`` role,
because that role only has table *grants*, not *ownership* — and only the table
OWNER may ``ALTER TABLE ... NO FORCE ROW LEVEL SECURITY``, which is exactly what
the migrate Job's owner connection does in prod. So this suite mints its own
``NOSUPERUSER NOBYPASSRLS`` role, makes it the OWNER of a throwaway FORCE-RLS
probe table (mirroring prod's migrate owner), and asserts:

* without the helper, an owner ``DELETE`` under FORCE RLS matches ZERO rows and
  raises nothing (the production bug), and
* wrapped in ``rls_bypassed`` the same ``DELETE`` removes the rows,
* FORCE RLS is restored afterward — including when the wrapped block raises,
* misuse (unknown / non-forced / invalid table, no tables) fails LOUD.

The helper is synchronous (it mirrors alembic's ``op.execute`` / ``op.get_bind``
surface), so this is a synchronous test that drives asyncpg through a dedicated
event loop; a tiny ``_Op`` shim presents the two methods the helper calls.
Skips when no Postgres is reachable, like the sibling RLS suites.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

from app.db.rls_migration import rls_bypassed

_RAW = os.environ.get("COMPANION_DATABASE_URL", "")
_PG = _RAW.replace("+asyncpg", "") if _RAW else ""

_OWNER_ROLE = "rlsmig_owner"
_OWNER_PW = "rls_owner_pw"
_FORCED = "rlsmig_probe"  # owned by _OWNER_ROLE, ENABLE + FORCE RLS
_PLAIN = "rlsmig_plain"  # owned by _OWNER_ROLE, no RLS (for the not-forced guard)
_UID = uuid.uuid4()

# Dedicated loop: the helper is sync, so the test is sync and drives asyncpg via
# run_until_complete (no outer running loop to nest under).
_loop = asyncio.new_event_loop()


def _run(coro):
    return _loop.run_until_complete(coro)


def _dsn_as(user: str, password: str) -> str:
    p = urlparse(_PG)
    netloc = f"{user}:{password}@{p.hostname}:{p.port or 5432}"
    return urlunparse((p.scheme, netloc, p.path, "", "", ""))


def _reachable() -> bool:
    if not _PG or asyncpg is None:
        return False
    try:
        c = _run(asyncpg.connect(_PG))
        _run(c.close())
        return True
    except Exception:
        return False


def _rowcount(tag: str) -> int:
    """asyncpg returns a command tag like 'DELETE 3' / 'UPDATE 0'."""
    return int(tag.split()[-1])


class _Op:
    """Minimal stand-in for alembic's ``op``: the two calls rls_bypassed makes.

    ``get_bind().exec_driver_sql(sql).scalar()`` (the forced-state probe) and
    ``execute(sql)`` (the ALTER toggles) — both routed to the owner connection.
    """

    def __init__(self, conn):
        self._conn = conn

    def get_bind(self):
        return self

    def exec_driver_sql(self, sql):
        return _Result(_run(self._conn.fetchval(sql)))

    def execute(self, sql):
        _run(self._conn.execute(str(sql)))


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


@pytest.fixture(scope="module")
def mig_env():
    if not _reachable():
        pytest.skip("no reachable Postgres for RLS migration-helper tests")

    su = _run(asyncpg.connect(_PG))  # superuser (companion) — bypasses RLS
    owner = None
    try:
        _run(su.execute(f"DROP TABLE IF EXISTS {_FORCED}"))
        _run(su.execute(f"DROP TABLE IF EXISTS {_PLAIN}"))
        _run(su.execute(f"DROP ROLE IF EXISTS {_OWNER_ROLE}"))
        _run(
            su.execute(
                f"CREATE ROLE {_OWNER_ROLE} LOGIN PASSWORD '{_OWNER_PW}' "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
        )
        _run(su.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {_OWNER_ROLE}"))

        # A FORCE-RLS probe table owned by the NOBYPASSRLS role — the exact shape
        # (per-user policy keyed on the app.current_user_id GUC) that bites the
        # migrate owner in prod. Owned by the role so it can ALTER ... NO FORCE.
        _run(su.execute(f"CREATE TABLE {_FORCED} (user_id uuid NOT NULL, val text)"))
        _run(su.execute(f"ALTER TABLE {_FORCED} ENABLE ROW LEVEL SECURITY"))
        _run(su.execute(f"ALTER TABLE {_FORCED} FORCE ROW LEVEL SECURITY"))
        _pred = (
            "user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
        )
        _run(
            su.execute(
                f"CREATE POLICY {_FORCED}_isolation ON {_FORCED} "
                f"USING ({_pred}) WITH CHECK ({_pred})"
            )
        )
        _run(su.execute(f"ALTER TABLE {_FORCED} OWNER TO {_OWNER_ROLE}"))

        # A sibling table with NO RLS, owned by the same role, for the
        # not-under-FORCE guard.
        _run(su.execute(f"CREATE TABLE {_PLAIN} (user_id uuid, val text)"))
        _run(su.execute(f"ALTER TABLE {_PLAIN} OWNER TO {_OWNER_ROLE}"))

        owner = _run(asyncpg.connect(_dsn_as(_OWNER_ROLE, _OWNER_PW)))
        yield {"su": su, "owner": owner}
    finally:
        if owner is not None:
            _run(owner.close())
        _run(su.execute(f"DROP TABLE IF EXISTS {_FORCED}"))
        _run(su.execute(f"DROP TABLE IF EXISTS {_PLAIN}"))
        _run(su.execute(f"DROP OWNED BY {_OWNER_ROLE}"))
        _run(su.execute(f"DROP ROLE IF EXISTS {_OWNER_ROLE}"))
        _run(su.close())


def _reseed(su, n: int = 2) -> None:
    """Reset the probe table to exactly ``n`` rows (as superuser → bypasses RLS)."""
    _run(su.execute(f"DELETE FROM {_FORCED}"))
    for _ in range(n):
        _run(su.execute(f"INSERT INTO {_FORCED} (user_id, val) VALUES ($1, 'x')", _UID))


def _forced(su) -> bool:
    return bool(
        _run(
            su.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = $1", _FORCED
            )
        )
    )


def _count(su) -> int:
    return _run(su.fetchval(f"SELECT count(*) FROM {_FORCED}"))


def test_owner_delete_without_helper_silently_noops(mig_env):
    """The production bug: owner DELETE under FORCE RLS, no GUC → 0 rows, no error."""
    su, owner = mig_env["su"], mig_env["owner"]
    _reseed(su, 2)
    tag = _run(owner.execute(f"DELETE FROM {_FORCED}"))
    assert _rowcount(tag) == 0  # RLS filtered every row out — silent no-op
    assert _count(su) == 2  # rows untouched, proving the harness has real FORCE RLS


def test_owner_delete_with_helper_affects_rows(mig_env):
    """With rls_bypassed the same owner DELETE removes the rows; FORCE is restored."""
    su, owner = mig_env["su"], mig_env["owner"]
    _reseed(su, 2)
    with rls_bypassed(_Op(owner), _FORCED):
        tag = _run(owner.execute(f"DELETE FROM {_FORCED}"))
        assert _rowcount(tag) == 2  # NO FORCE → owner exempt → rows actually deleted
    assert _count(su) == 0
    assert _forced(su) is True  # FORCE restored on exit


def test_owner_update_with_helper_affects_rows(mig_env):
    """Mirrors migration 044 (UPDATE ... SET col = NULL) under the helper."""
    su, owner = mig_env["su"], mig_env["owner"]
    _reseed(su, 3)
    with rls_bypassed(_Op(owner), _FORCED):
        tag = _run(owner.execute(f"UPDATE {_FORCED} SET val = NULL"))
        assert _rowcount(tag) == 3
    nulls = _run(su.fetchval(f"SELECT count(*) FROM {_FORCED} WHERE val IS NULL"))
    assert nulls == 3
    assert _forced(su) is True


def test_force_restored_after_wrapped_block_raises(mig_env):
    """The finally re-forces even when the wrapped DML raises."""
    su, owner = mig_env["su"], mig_env["owner"]
    _reseed(su, 2)
    with pytest.raises(ValueError, match="boom"):
        with rls_bypassed(_Op(owner), _FORCED):
            raise ValueError("boom")
    assert _forced(su) is True
    # And prove FORCE is truly back in effect: the owner is filtered again.
    tag = _run(owner.execute(f"DELETE FROM {_FORCED}"))
    assert _rowcount(tag) == 0
    assert _count(su) == 2


def test_rejects_table_not_under_force(mig_env):
    """A table that exists but isn't FORCE-RLS fails loud (would silently force it)."""
    owner = mig_env["owner"]
    with pytest.raises(ValueError, match="not under FORCE"):
        with rls_bypassed(_Op(owner), _PLAIN):
            pass


def test_rejects_unknown_table(mig_env):
    owner = mig_env["owner"]
    with pytest.raises(ValueError, match="does not exist"):
        with rls_bypassed(_Op(owner), "rlsmig_definitely_absent"):
            pass


def test_rejects_invalid_identifier(mig_env):
    owner = mig_env["owner"]
    with pytest.raises(ValueError, match="unsafe/invalid"):
        with rls_bypassed(_Op(owner), "chat_messages; DROP TABLE users"):
            pass


def test_requires_at_least_one_table(mig_env):
    owner = mig_env["owner"]
    with pytest.raises(ValueError, match="at least one table"):
        with rls_bypassed(_Op(owner)):
            pass
