"""RLS-safe DML for FORCE-ROW-LEVEL-SECURITY tenant tables inside migrations.

WHY THIS EXISTS
---------------
The migrate Job runs ``alembic upgrade head`` connected as the *table owner*.
The 18 tenant tables carry ``FORCE ROW LEVEL SECURITY`` (``relforcerowsecurity
= true``), which — unlike plain ``ENABLE`` — subjects the owner to RLS too. A
migration sets no ``app.current_user_id`` GUC, so the per-user policy
(``USING (user_id = current_setting('app.current_user_id'))``) evaluates the
GUC to NULL and filters EVERY row out. The result is the nastiest possible
failure mode: any ``UPDATE`` / ``DELETE`` / visibility-dependent ``INSERT
... SELECT`` on a tenant table matches **zero rows**, raises **no error**, and
the migration is still recorded as applied. (Confirmed in prod on revisions
044 and 045 — both silently did nothing.) DDL is unaffected; this is strictly
about DML on FORCE-RLS tenant tables.

THE FIX (owner-executable, atomic)
----------------------------------
The table owner may always toggle its own table's RLS. So we temporarily
``ALTER TABLE <t> NO FORCE ROW LEVEL SECURITY`` — which makes the *owner* exempt
from the policy again (RLS still applies to everyone else) — run the DML, then
restore ``FORCE``. Everything runs inside the migration's single transaction
(alembic wraps ``run_migrations`` in ``begin_transaction``), so the toggle is
atomic with the DML: on rollback the ``NO FORCE`` never commits. The re-``FORCE``
is in a ``finally`` so a raising DML cannot leave a tenant table exposed even
for the rest of the (doomed) transaction.

REJECTED ALTERNATIVES
---------------------
* ``SET row_security = off`` — does NOT work here. For a non-``BYPASSRLS`` role
  under ``FORCE`` RLS, Postgres *errors* ("query would be affected by row-level
  security policy") rather than bypassing; it is only an escape hatch for roles
  that already carry the ``BYPASSRLS`` attribute. The migrate owner is
  ``NOBYPASSRLS`` by design.
* Setting ``app.current_user_id`` in a per-user loop — impractical for cross-
  tenant bulk maintenance (would require iterating every member and re-issuing
  the DML per GUC value, and still can't express a single set-based statement).
* Running migrations as a ``BYPASSRLS``/superuser role — widens the migrate
  blast radius and diverges from the least-privilege owner the Job already uses.

USAGE
-----
    from app.db.rls_migration import rls_bypassed

    def upgrade() -> None:
        with rls_bypassed(op, "chat_messages", "chat_sessions"):
            op.execute("DELETE FROM chat_messages")
            op.execute("DELETE FROM chat_sessions")

Only wrap the DML; keep DDL outside the block (it never needed this).

Lives in ``app/db`` (not ``alembic/``) so migrations can ``import`` it the same
way they already ``import`` the policy shapes from ``app.db.rls`` — the local
``alembic/`` directory is not a package, so ``from alembic.rls_migration``
would resolve to the *installed* alembic package and fail. This also colocates
it with the rest of the live RLS contract: ``app/db/rls.py`` (policy shape),
``app/db/context.py`` (the tenant GUC) and ``app/db/rls_guard.py`` (the runtime
unset-GUC guard).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

# Tenant table names are supplied by migration authors, never end users, but we
# still fence the identifier so it can be interpolated into DDL safely (there is
# no bind-parameter form for an identifier in ``ALTER TABLE``).
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate(table: str) -> str:
    if not isinstance(table, str) or not _IDENT_RE.match(table):
        raise ValueError(f"unsafe/invalid table identifier for rls_bypassed: {table!r}")
    return table


def _is_forced(op, table: str) -> bool:
    """Whether ``table`` currently has FORCE ROW LEVEL SECURITY."""
    forced = (
        op.get_bind()
        .exec_driver_sql(
            f"SELECT relforcerowsecurity FROM pg_class WHERE relname = '{table}'"
        )
        .scalar()
    )
    if forced is None:
        raise ValueError(
            f"rls_bypassed: table {table!r} does not exist in the current database"
        )
    return bool(forced)


@contextmanager
def rls_bypassed(op, *tables: str) -> Iterator[None]:
    """Temporarily drop FORCE RLS on ``tables`` so owner-run DML is not filtered.

    Yields with FORCE disabled on each named tenant table, then unconditionally
    restores FORCE in ``finally`` — even if the wrapped DML raises. Runs inside
    the caller's (migration) transaction, so the toggle is atomic with the DML.

    Fails LOUD, not silent, on misuse: a table that does not exist, or that is
    not currently under FORCE RLS, raises ``ValueError`` rather than silently
    toggling state the caller did not intend. (If a table is legitimately only
    ``ENABLE``-without-``FORCE``, the owner already bypasses its policy and this
    helper is unnecessary.)
    """
    if not tables:
        raise ValueError("rls_bypassed requires at least one table")

    validated = [_validate(t) for t in tables]
    for table in validated:
        if not _is_forced(op, table):
            raise ValueError(
                f"rls_bypassed: table {table!r} is not under FORCE ROW LEVEL "
                "SECURITY — refuse to toggle (owner already bypasses its policy)"
            )

    for table in validated:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    try:
        yield
    finally:
        # Restore FORCE even if the DML raised. On a raising DML the whole
        # transaction still rolls back (so the NO FORCE never commits either),
        # but re-forcing here keeps the table protected for any later statement
        # in the same doomed transaction and makes the invariant explicit.
        for table in validated:
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
