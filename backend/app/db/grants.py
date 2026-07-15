"""Grant DML to the non-owner runtime role (WS1 Phase 1).

Runs as a step of the migrate Job, AFTER ``alembic upgrade head``:

    command: ["sh", "-c", "alembic upgrade head && python -m app.db.grants"]

Both steps connect as the OWNER ``companion`` (the CNPG-generated
``companion-db-app`` secret). alembic creates/updates tables (and, in Phase 2,
RLS policies); this step then grants the separate NON-owner runtime role
``companion_app`` (created by CNPG ``managed.roles``) DML on every table plus
default privileges so future migrations' tables are auto-granted.

Why a separate step and not an alembic migration (per HCC/kali): CNPG reconciles
``managed.roles`` asynchronously, so the role may not exist the instant alembic
finishes. Folding the wait into the grants step keeps the two async timelines
(alembic DDL vs CNPG role reconcile) decoupled and self-healing. The grants are
idempotent and re-run every deploy on purpose:
- ``GRANT ... ON ALL TABLES`` catches anything created before ALTER DEFAULT
  PRIVILEGES existed;
- ``ALTER DEFAULT PRIVILEGES`` makes every FUTURE owner-created table auto-grant
  (without it the app 'permission denied's on the newest table until re-granted).

``companion_app`` gets full DML on every table EXCEPT the append-only audit tables
(``caregiver_activity_log``, ``account_audit_log``), which are narrowed back to
INSERT + SELECT immediately after the broad grant (see ``_REVOKE_STATEMENTS``).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

# The non-owner runtime role. Kept in sync with gitops db-cluster.yaml
# (managed.roles) and the api/worker connection secret (companion-db-appuser).
APP_ROLE = "companion_app"

# CNPG reconciles managed.roles asynchronously; poll before granting.
_ROLE_POLL_RETRIES = 12
_ROLE_POLL_DELAY_S = 5.0  # 12 * 5s = 60s budget

# Idempotent. Role name is a trusted module constant (not user input); GRANT
# cannot be parameterized, so it is interpolated directly.
_GRANT_STATEMENTS = (
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}",
)

# Append-only audit tables: the runtime role may INSERT + SELECT but must NOT
# UPDATE/DELETE, so an app bug or a compromised companion_app cannot tamper with or
# erase the audit trail (docs/caregiver-access-and-privacy.md §5 + Appendix C:
# "caregiver_activity_log is append-only — no UPDATE or DELETE grants"). Applied AFTER
# the broad GRANT above, and because this whole step re-runs every deploy the REVOKE is
# self-healing (a one-shot migration would be re-granted by the next ON ALL TABLES).
# NOTE on the maintenance role: companion_maintenance is a MEMBER of companion_app
# (gitops db-cluster.yaml managed.roles -> inRoles), so it INHERITS this append-only
# REVOKE — it is NOT automatically exempt. retention's purge of old signup_refused rows
# runs under that role (app/workers/retention.py), so it DOES need DELETE on
# account_audit_log; we re-grant that directly below (see _MAINT_REGRANT_STATEMENTS).
# A direct grant is the UNION with the inherited set, so companion_app itself stays
# append-only (INSERT + SELECT only) while only the server-side maintenance role can
# purge. (An earlier version of this comment wrongly assumed the maintenance role was
# unaffected; the membership inheritance made retention 500 on permission-denied.)
# NOTE on what this does NOT block, by design:
#  - a user/trusted_contact deletion erases caregiver_activity_log via the FK's DB-level
#    ON DELETE CASCADE — a referential action run as the table OWNER, so it bypasses this
#    role-level REVOKE. This holds ONLY because the ORM relationships to
#    caregiver_activity_log set passive_deletes=True (User.caregiver_activity_logs,
#    TrustedContact.activity_logs), which makes SQLAlchemy defer to the DB cascade
#    instead of emitting an ORM DELETE as companion_app. Without passive_deletes the
#    grace=0 member self-serve deletion would 500 on permission-denied.
_APPEND_ONLY_AUDIT_TABLES = ("caregiver_activity_log", "account_audit_log")
_REVOKE_STATEMENTS = tuple(
    f"REVOKE UPDATE, DELETE ON {table} FROM {APP_ROLE}"
    for table in _APPEND_ONLY_AUDIT_TABLES
)

# The BYPASSRLS maintenance role (gitops db-cluster.yaml managed.roles; a member of
# APP_ROLE). It needs DELETE on account_audit_log ONLY so the cross-user retention
# worker can purge transient signup_refused rows (app/workers/retention.py); the app
# WHERE-clause scopes the deletion to those rows. caregiver_activity_log is deliberately
# NOT re-granted here — it is only ever purged via the owner-run FK ON DELETE CASCADE,
# never a maintenance DELETE — so it stays fully immutable for every runtime role.
# SECURITY NOTE: companion_maintenance is NOT cron-only — it also backs the admin HTTP
# surface via get_maintenance_db (app/api/admin/*). So this table-level grant makes the
# signup_refused scope enforced SOLELY by the app WHERE-clause, not the DB: under a bug
# in any admin-session path the role could delete account_activated (real-member) audit
# rows. This is the pragmatic fix that unblocks the deploy deadlock; the REQUIRED pre-PHI
# hardening is to replace it with a table-owner SECURITY DEFINER function scoped to
# event='signup_refused' + REVOKE table-level DELETE from every runtime role, so the
# scope is DB-enforced. Tracked as the account_audit_log half of the audit-immutability
# launch gate. Do NOT onboard real PHI members with this table-level grant still in place.
MAINT_ROLE = "companion_maintenance"
_MAINT_REGRANT_STATEMENTS = (
    f"GRANT DELETE ON account_audit_log TO {MAINT_ROLE}",
)


async def _role_exists(conn, role: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
    )
    return bool(result.scalar())


async def apply_grants(
    *, retries: int = _ROLE_POLL_RETRIES, delay: float = _ROLE_POLL_DELAY_S
) -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for attempt in range(1, retries + 1):
                if await _role_exists(conn, APP_ROLE):
                    break
                logger.info(
                    "grants: role %r not present yet (attempt %d/%d); "
                    "waiting %.0fs for CNPG managed.roles",
                    APP_ROLE,
                    attempt,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(
                    f"runtime role {APP_ROLE!r} did not appear within "
                    f"{retries * delay:.0f}s — check CNPG managed.roles + the "
                    "companion-db-appuser sealed secret (WS1 Phase 1)."
                )

            for stmt in _GRANT_STATEMENTS:
                await conn.execute(text(stmt))
            # Then narrow the audit tables back to append-only (INSERT + SELECT).
            for stmt in _REVOKE_STATEMENTS:
                await conn.execute(text(stmt))
            # Re-grant the maintenance role DELETE on account_audit_log so the retention
            # purge works despite inheriting the append-only REVOKE from APP_ROLE. Guarded
            # on role existence — absent in dev/test, where retention falls back to the
            # app session and no maintenance role exists.
            maint_regranted = await _role_exists(conn, MAINT_ROLE)
            if maint_regranted:
                for stmt in _MAINT_REGRANT_STATEMENTS:
                    await conn.execute(text(stmt))
        logger.info(
            "grants: applied DML + default privileges to %r; audit tables %s are "
            "append-only (UPDATE/DELETE revoked)%s",
            APP_ROLE,
            list(_APPEND_ONLY_AUDIT_TABLES),
            f"; re-granted DELETE on account_audit_log to {MAINT_ROLE}"
            if maint_regranted
            else "",
        )
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(apply_grants())


if __name__ == "__main__":
    main()
