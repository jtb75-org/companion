"""Enable per-user RLS on device_tokens (WS1 Phase 2e)

Revision ID: 026
Revises: 025

device_tokens was excluded from the 025 batch because register_token performed
a cross-user lookup/reassignment of the globally-unique fcm_token (a device
moving between users) — flat RLS hides the other user's row and the insert hits
the unique constraint (niru, PR #50 review). The service now handles the
cross-tenant release via a scoped maintenance-session helper
(_release_token_other_user), so the flat per-user policy applies cleanly.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "026"
down_revision = "025"


def upgrade() -> None:
    for stmt in tenant_isolation_statements("device_tokens"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in drop_isolation_statements("device_tokens"):
        op.execute(stmt)
