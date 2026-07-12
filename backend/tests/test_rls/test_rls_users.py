"""Adversarial RLS suite for the `users` table's dual-clause policy (029).

`users` is the one bootstrap table: readable when id matches app.current_user_id
OR email matches app.current_login_email, but writable only when
id = app.current_user_id. The security-critical property this suite locks is the
ASYMMETRY — the email bootstrap admits a READ but must NEVER admit a WRITE — plus
the standard fail-closed / own-only / maintenance-bypass guarantees.

Same harness shape as test_rls_isolation: connects to a real Postgres (CI
provides one; skips otherwise), creating its own NOSUPERUSER NOBYPASSRLS app role
and a BYPASSRLS maintenance role, since CI's `companion` superuser bypasses RLS.
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

_APP_ROLE = "rlsusr_app"
_MAINT_ROLE = "rlsusr_maint"
_APP_PW = "rls_app_pw"
_MAINT_PW = "rls_maint_pw"

_UID_A = uuid.uuid4()
_UID_B = uuid.uuid4()
_EMAIL_A = f"rlsusr-{_UID_A}@t.io"
_EMAIL_B = f"rlsusr-{_UID_B}@t.io"


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


@pytest.fixture(scope="module")
async def rls_users_env():
    if not await _reachable():
        pytest.skip("no reachable Postgres for RLS tests")

    su = await asyncpg.connect(_PG)  # superuser (companion) — bypasses RLS
    try:
        forced = await su.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname='users'"
        )
        if not forced:
            pytest.skip("users not under FORCE RLS in this DB (029 not applied)")

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
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON users TO {role}"
            )

        for uid, email in ((_UID_A, _EMAIL_A), (_UID_B, _EMAIL_B)):
            await su.execute(
                "INSERT INTO users (id, email, preferred_name, display_name) "
                "VALUES ($1, $2, 'P', 'P') ON CONFLICT (id) DO NOTHING",
                uid,
                email,
            )
        yield {}
    finally:
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


async def _set_email(conn, email) -> None:
    await conn.execute(
        "SELECT set_config('app.current_login_email', $1, false)", email
    )


async def test_unset_gucs_fail_closed(rls_users_env):
    conn = await _app_conn()
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        assert n == 0
    finally:
        await conn.close()


async def test_login_email_guc_reads_own_by_email(rls_users_env):
    conn = await _app_conn()
    try:
        await _set_email(conn, _EMAIL_A)
        rows = await conn.fetch(
            "SELECT id FROM users WHERE id = ANY($1::uuid[])", [_UID_A, _UID_B]
        )
        assert [r["id"] for r in rows] == [_UID_A]
    finally:
        await conn.close()


async def test_user_id_guc_reads_own_by_id(rls_users_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        rows = await conn.fetch(
            "SELECT id FROM users WHERE id = ANY($1::uuid[])", [_UID_A, _UID_B]
        )
        assert [r["id"] for r in rows] == [_UID_A]
    finally:
        await conn.close()


async def test_email_bootstrap_cannot_write(rls_users_env):
    """The security-critical asymmetry: an email-bootstrapped session may READ
    its row but the WITH CHECK (id = user-id GUC, which is unset→NULL) must
    reject any UPDATE to it."""
    conn = await _app_conn()
    try:
        await _set_email(conn, _EMAIL_A)  # read-bootstrap only; no user-id GUC
        # The row IS visible (USING passes)...
        assert await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = $1", _UID_A
        ) == 1
        # ...but the UPDATE post-image fails WITH CHECK (id = NULL).
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "UPDATE users SET preferred_name = 'hacked' WHERE id = $1", _UID_A
            )
    finally:
        await conn.close()


async def test_self_write_allowed(rls_users_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        await conn.execute(
            "UPDATE users SET preferred_name = 'self' WHERE id = $1", _UID_A
        )
        assert await conn.fetchval(
            "SELECT preferred_name FROM users WHERE id = $1", _UID_A
        ) == "self"
    finally:
        await conn.close()


async def test_write_for_other_user_rejected(rls_users_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        # B's row is invisible under USING → UPDATE matches 0 rows (no error),
        # and an attempt to flip an id to B would fail WITH CHECK. Prove the
        # cross-user UPDATE changes nothing.
        await conn.execute(
            "UPDATE users SET preferred_name = 'x' WHERE id = $1", _UID_B
        )
        # Confirm via bypass that B is untouched.
        m = await asyncpg.connect(_dsn_as(_MAINT_ROLE, _MAINT_PW))
        try:
            assert await m.fetchval(
                "SELECT preferred_name FROM users WHERE id = $1", _UID_B
            ) == "P"
        finally:
            await m.close()
    finally:
        await conn.close()


async def test_maintenance_bypass_sees_all(rls_users_env):
    conn = await asyncpg.connect(_dsn_as(_MAINT_ROLE, _MAINT_PW))
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        assert n == 2
    finally:
        await conn.close()
