"""Enable per-user RLS on functional_memory (WS1 Phase 2e)

Revision ID: 024
Revises: 023

The second table under RLS (after todos). The batch access-path audit found
functional_memory is fully GUC-covered already: it is only reached via the
member API (users.py memory GET/DELETE, under get_current_user) and the
conversation prompt_builder read (member GUC); `upsert_memory` has no callers and
`delete_all_memories` runs only via account deletion (maintenance BYPASSRLS) or
cascade. Standard-13 direct-user_id table → the flat per-user policy applies.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "024"
down_revision = "023"


def upgrade() -> None:
    for stmt in tenant_isolation_statements("functional_memory"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_isolation_statements("functional_memory"):
        op.execute(stmt)
