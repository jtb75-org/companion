"""Enable per-user RLS on todos (WS1 Phase 2d — first live policy)

Revision ID: 023
Revises: 022

The first table to go under Row-Level Security, chosen because it is a clean
"standard-13" tenant table (direct user_id, only ever read member-scoped) and
every access path already sets app.current_user_id: the member API + conversation
tools (via get_current_user), the caregiver read paths (via get_current_caregiver,
member-id-as-context), and the morning worker (run_morning_trigger_for_user sets
the GUC). See docs/phi-rls-phase2-design.md.

Fail-closed: any query without the GUC set returns zero rows / rejects writes.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "023"
down_revision = "022"


def upgrade() -> None:
    for stmt in tenant_isolation_statements("todos"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_isolation_statements("todos"):
        op.execute(stmt)
