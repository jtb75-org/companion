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

RLS is NOT enabled here (that is Phase 2); until then ``companion_app`` simply
has full DML and the app behaves exactly as today.
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
        logger.info("grants: applied DML + default privileges to %r", APP_ROLE)
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(apply_grants())


if __name__ == "__main__":
    main()
