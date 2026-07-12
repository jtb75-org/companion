"""Tighten user_id NOT NULL + enable RLS on chat_messages, medication_confirmations

Revision ID: 027
Revises: 026

The two denormalized child tables (022 added user_id + backfill + the
enforce_same_user trigger; write paths set user_id). Now that all writers
populate it, tighten to NOT NULL and enable the standard flat per-user policy
(app/db/rls.py). A defensive backfill re-runs first in case any row slipped
through with a NULL between 022 and now (parent user_id is the source of truth).
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "027"
down_revision = "026"

_TABLES = (
    ("chat_messages", "chat_sessions", "chat_session_id"),
    ("medication_confirmations", "medications", "medication_id"),
)


def upgrade() -> None:
    for table, parent, fk in _TABLES:
        # Defensive backfill of any stragglers from the parent, then lock it in.
        op.execute(
            f"UPDATE {table} c SET user_id = p.user_id "
            f"FROM {parent} p WHERE p.id = c.{fk} AND c.user_id IS NULL"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL")
        for stmt in tenant_isolation_statements(table):
            op.execute(stmt)


def downgrade() -> None:
    for table, _parent, _fk in _TABLES:
        for stmt in drop_isolation_statements(table):
            op.execute(stmt)
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id DROP NOT NULL")
