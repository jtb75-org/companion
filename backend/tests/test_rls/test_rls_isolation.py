"""Adversarial per-user RLS regression suite (WS1 Phase 2f).

Runs against a REAL Postgres with the migrations applied (CI provides one; the
module skips when no reachable DB). CI connects as the `companion` superuser,
which BYPASSES RLS — so this suite creates its own NON-owner NOSUPERUSER
NOBYPASSRLS role (to exercise enforcement) and a BYPASSRLS role (to prove the
maintenance path sees cross-user), mirroring prod's companion_app /
companion_maintenance split.

Guards the live policies against regression: unset GUC → 0, wrong GUC → 0,
correct GUC → only own rows, mismatched-owner write → rejected (WITH CHECK),
bypass role → sees everyone. Uses functional_memory as the probe (a real
standard-13 RLS table).
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None

pytestmark = pytest.mark.asyncio

_RAW = os.environ.get("COMPANION_DATABASE_URL", "")
_PG = _RAW.replace("+asyncpg", "") if _RAW else ""

_APP_ROLE = "rlstest_app"
_MAINT_ROLE = "rlstest_maint"
_APP_PW = "rls_app_pw"
_MAINT_PW = "rls_maint_pw"

_UID_A = uuid.uuid4()
_UID_B = uuid.uuid4()


def _dsn_as(user: str, password: str) -> str:
    p = urlparse(_PG)
    netloc = f"{user}:{password}@{p.hostname}:{p.port or 5432}"
    return urlunparse((p.scheme, netloc, p.path, "", "", ""))


async def _reachable() -> bool:
    if not _PG or asyncpg is None:
        return False
    try:
        c = await asyncpg.connect(_PG)
        await c.close()
        return True
    except Exception:
        return False


async def _enum_label(conn, typname: str) -> str:
    return await conn.fetchval(
        "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = $1 ORDER BY e.enumsortorder LIMIT 1",
        typname,
    )


@pytest.fixture(scope="module")
async def rls_env():
    if not await _reachable():
        pytest.skip("no reachable Postgres for RLS tests")

    su = await asyncpg.connect(_PG)  # superuser (companion) — bypasses RLS
    try:
        # Confirm functional_memory is actually under RLS (else the suite is moot).
        forced = await su.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname='functional_memory'"
        )
        if not forced:
            pytest.skip("functional_memory not under FORCE RLS in this DB")

        for role, pw, extra in (
            (_APP_ROLE, _APP_PW, "NOSUPERUSER NOBYPASSRLS"),
            (_MAINT_ROLE, _MAINT_PW, "NOSUPERUSER BYPASSRLS"),
        ):
            await su.execute(f"DROP ROLE IF EXISTS {role}")
            await su.execute(
                f"CREATE ROLE {role} LOGIN PASSWORD '{pw}' {extra} "
                "NOCREATEDB NOCREATEROLE"
            )
            await su.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
            await su.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON functional_memory TO {role}"
            )

        cat = await _enum_label(su, "memorycategory")
        src = await _enum_label(su, "memorysource")

        # Seed two members + one memory each (as superuser → bypasses RLS/WITH CHECK).
        for uid in (_UID_A, _UID_B):
            await su.execute(
                "INSERT INTO users (id, email, preferred_name, display_name) "
                "VALUES ($1, $2, 'P', 'P') ON CONFLICT (id) DO NOTHING",
                uid,
                f"rls-{uid}@t.io",
            )
            await su.execute(
                "INSERT INTO functional_memory (id, user_id, category, key, value, source) "
                "VALUES (gen_random_uuid(), $1, $2, 'k', 'v', $3)",
                uid,
                cat,
                src,
            )
        yield {"cat": cat, "src": src}
    finally:
        await su.execute(
            "DELETE FROM functional_memory WHERE user_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        await su.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[])", [_UID_A, _UID_B]
        )
        for role in (_APP_ROLE, _MAINT_ROLE):
            # Revoke grants (DROP OWNED) before dropping, else the role is pinned.
            await su.execute(f"DROP OWNED BY {role}")
            await su.execute(f"DROP ROLE IF EXISTS {role}")
        await su.close()


async def _app_conn():
    return await asyncpg.connect(_dsn_as(_APP_ROLE, _APP_PW))


async def _set_guc(conn, uid) -> None:
    await conn.execute("SELECT set_config('app.current_user_id', $1, false)", str(uid))


async def test_unset_guc_fails_closed(rls_env):
    conn = await _app_conn()
    try:
        assert await conn.fetchval("SELECT count(*) FROM functional_memory") == 0
    finally:
        await conn.close()


async def test_correct_guc_sees_only_own(rls_env):
    conn = await _app_conn()
    try:
        await _set_guc(conn, _UID_A)
        rows = await conn.fetch("SELECT user_id FROM functional_memory")
        assert len(rows) == 1
        assert rows[0]["user_id"] == _UID_A
    finally:
        await conn.close()


async def test_wrong_guc_returns_zero(rls_env):
    conn = await _app_conn()
    try:
        await _set_guc(conn, uuid.uuid4())  # a member with no rows
        assert await conn.fetchval("SELECT count(*) FROM functional_memory") == 0
    finally:
        await conn.close()


async def test_write_for_other_user_rejected(rls_env):
    conn = await _app_conn()
    try:
        await _set_guc(conn, _UID_A)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO functional_memory (id, user_id, category, key, value, source) "
                "VALUES (gen_random_uuid(), $1, $2, 'k', 'v', $3)",
                _UID_B,  # not the GUC owner → WITH CHECK must reject
                rls_env["cat"],
                rls_env["src"],
            )
    finally:
        await conn.close()


async def test_write_for_self_allowed(rls_env):
    conn = await _app_conn()
    try:
        await _set_guc(conn, _UID_A)
        await conn.execute(
            "INSERT INTO functional_memory (id, user_id, category, key, value, source) "
            "VALUES (gen_random_uuid(), $1, $2, 'own', 'v', $3)",
            _UID_A,
            rls_env["cat"],
            rls_env["src"],
        )
        n = await conn.fetchval(
            "SELECT count(*) FROM functional_memory WHERE key='own'"
        )
        assert n == 1
        await conn.execute("DELETE FROM functional_memory WHERE key='own'")
    finally:
        await conn.close()


async def test_maintenance_bypass_sees_all(rls_env):
    conn = await asyncpg.connect(_dsn_as(_MAINT_ROLE, _MAINT_PW))
    try:
        # No GUC set; BYPASSRLS role sees both members' rows.
        n = await conn.fetchval(
            "SELECT count(*) FROM functional_memory WHERE user_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        assert n == 2
    finally:
        await conn.close()
