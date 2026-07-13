"""Adversarial RLS suite for trusted_contacts (migration 030).

trusted_contacts uses the STANDARD flat per-user policy (user_id = the member).
The property this suite locks — and the reason caregiver auth was routed to the
maintenance session — is that a by-`contact_email` caregiver-auth read returns
rows ONLY under the BYPASSRLS role; on a companion_app session it fails closed
unless the member GUC is the row owner. Plus fail-closed / own-only / cross-user
write rejected.

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

_APP_ROLE = "rlstc_app"
_MAINT_ROLE = "rlstc_maint"
_APP_PW = "rls_app_pw"
_MAINT_PW = "rls_maint_pw"

_UID_A = uuid.uuid4()
_UID_B = uuid.uuid4()
_CG_EMAIL = "caregiver@t.io"  # same caregiver invited by BOTH members


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
async def rls_tc_env():
    if not await _reachable():
        pytest.skip("no reachable Postgres for RLS tests")

    su = await asyncpg.connect(_PG)
    try:
        forced = await su.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname='trusted_contacts'"
        )
        if not forced:
            pytest.skip("trusted_contacts not under FORCE RLS (030 not applied)")

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
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON trusted_contacts TO {role}"
            )

        rel = await _enum_label(su, "relationshiptype")
        tier = await _enum_label(su, "accesstier")

        for uid in (_UID_A, _UID_B):
            await su.execute(
                "INSERT INTO users (id, email, preferred_name, display_name) "
                "VALUES ($1, $2, 'P', 'P') ON CONFLICT (id) DO NOTHING",
                uid,
                f"tcmember-{uid}@t.io",
            )
            # Each member invited the SAME caregiver email (a real multi-member case).
            await su.execute(
                "INSERT INTO trusted_contacts "
                "(id, user_id, contact_name, contact_email, relationship_type, "
                " access_tier, is_active, invitation_status) "
                "VALUES (gen_random_uuid(), $1, 'CG', $2, $3, $4, true, 'accepted')",
                uid,
                _CG_EMAIL,
                rel,
                tier,
            )
        yield {"rel": rel, "tier": tier}
    finally:
        await su.execute(
            "DELETE FROM trusted_contacts WHERE user_id = ANY($1::uuid[])",
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


async def test_unset_guc_fails_closed(rls_tc_env):
    conn = await _app_conn()
    try:
        assert await conn.fetchval(
            "SELECT count(*) FROM trusted_contacts WHERE user_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        ) == 0
    finally:
        await conn.close()


async def test_member_guc_sees_only_own(rls_tc_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        rows = await conn.fetch(
            "SELECT user_id FROM trusted_contacts WHERE user_id = ANY($1::uuid[])",
            [_UID_A, _UID_B],
        )
        assert [r["user_id"] for r in rows] == [_UID_A]
    finally:
        await conn.close()


async def test_caregiver_email_read_needs_bypass(rls_tc_env):
    """The by-contact_email caregiver-auth read: on a companion_app session with
    NO member GUC it returns 0 (why auth was routed to maintenance); the BYPASSRLS
    role sees BOTH members' relationships for this caregiver."""
    app = await _app_conn()
    try:
        assert await app.fetchval(
            "SELECT count(*) FROM trusted_contacts WHERE contact_email = $1",
            _CG_EMAIL,
        ) == 0
    finally:
        await app.close()

    m = await asyncpg.connect(_dsn_as(_MAINT_ROLE, _MAINT_PW))
    try:
        assert await m.fetchval(
            "SELECT count(*) FROM trusted_contacts WHERE contact_email = $1",
            _CG_EMAIL,
        ) == 2
    finally:
        await m.close()


async def test_cross_member_write_rejected(rls_tc_env):
    conn = await _app_conn()
    try:
        await _set_uid(conn, _UID_A)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO trusted_contacts "
                "(id, user_id, contact_name, contact_email, relationship_type, "
                " access_tier, is_active, invitation_status) "
                "VALUES (gen_random_uuid(), $1, 'X', 'x@t.io', $2, $3, true, 'accepted')",
                _UID_B,  # not the GUC owner → WITH CHECK rejects
                rls_tc_env["rel"],
                rls_tc_env["tier"],
            )
    finally:
        await conn.close()
