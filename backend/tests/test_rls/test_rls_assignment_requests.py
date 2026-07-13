"""Adversarial RLS suite for caregiver_assignment_requests (migration 031).

The final WS1 bootstrap table. Standard flat policy keyed on `member_id` (the
member the request targets; NOT NULL). Locks fail-closed / member-only / cross-
member-write-rejected + maintenance bypass (the admin create/approve/list path).

Same harness as test_rls_isolation: real Postgres (skips if none), own
NOSUPERUSER NOBYPASSRLS + BYPASSRLS roles (CI's superuser bypasses RLS).
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

_APP_ROLE = "rlsar_app"
_MAINT_ROLE = "rlsar_maint"
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
async def rls_ar_env():
    if not await _reachable():
        pytest.skip("no reachable Postgres for RLS tests")

    su = await asyncpg.connect(_PG)
    try:
        forced = await su.fetchval(
            "SELECT relforcerowsecurity FROM pg_class "
            "WHERE relname='caregiver_assignment_requests'"
        )
        if not forced:
            pytest.skip("caregiver_assignment_requests not under FORCE RLS (031)")

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
                "GRANT SELECT, INSERT, UPDATE, DELETE ON "
                f"caregiver_assignment_requests TO {role}"
            )

        rel = await _enum_label(su, "relationshiptype")
        tier = await _enum_label(su, "accesstier")

        for uid in (_UID_A, _UID_B):
            await su.execute(
                "INSERT INTO users (id, email, preferred_name, display_name) "
                "VALUES ($1, $2, 'P', 'P') ON CONFLICT (id) DO NOTHING",
                uid,
                f"armember-{uid}@t.io",
            )
            await su.execute(
                "INSERT INTO caregiver_assignment_requests "
                "(id, member_id, caregiver_email, caregiver_name, relationship_type, "
                " access_tier, status, initiated_by) "
                "VALUES (gen_random_uuid(), $1, 'cg@t.io', 'CG', $2, $3, "
                "'pending_approval', 'caregiver')",
                uid,
                rel,
                tier,
            )
        yield {"rel": rel, "tier": tier}
    finally:
        await su.execute(
            "DELETE FROM caregiver_assignment_requests WHERE member_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        await su.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[])", [_UID_A, _UID_B]
        )
        for role in (_APP_ROLE, _MAINT_ROLE):
            await su.execute(f"DROP OWNED BY {role}")
            await su.execute(f"DROP ROLE IF EXISTS {role}")
        await su.close()


async def _app_conn():
    return await asyncpg.connect(_dsn_as(_APP_ROLE, _APP_PW))


async def _set_uid(conn, uid) -> None:
    await conn.execute("SELECT set_config('app.current_user_id', $1, false)", str(uid))


async def test_unset_guc_fails_closed(rls_ar_env):
    conn = await _app_conn()
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM caregiver_assignment_requests "
            "WHERE member_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        ) == 0
    finally:
        await conn.close()


async def test_member_guc_sees_only_own(rls_ar_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        rows = await conn.fetch(
            "SELECT member_id FROM caregiver_assignment_requests "
            "WHERE member_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        assert [r["member_id"] for r in rows] == [_UID_A]
    finally:
        await conn.close()


async def test_cross_member_write_rejected(rls_ar_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO caregiver_assignment_requests "
                "(id, member_id, caregiver_email, caregiver_name, relationship_type, "
                " access_tier, status, initiated_by) "
                "VALUES (gen_random_uuid(), $1, 'x@t.io', 'X', $2, $3, "
                "'pending_approval', 'caregiver')",
                _UID_B,  # not the GUC owner → WITH CHECK rejects
                rls_ar_env["rel"],
                rls_ar_env["tier"],
            )
    finally:
        await conn.close()


async def test_maintenance_bypass_sees_all(rls_ar_env):
    conn = await asyncpg.connect(_dsn_as(_MAINT_ROLE, _MAINT_PW))
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM caregiver_assignment_requests "
            "WHERE member_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        ) == 2
    finally:
        await conn.close()
