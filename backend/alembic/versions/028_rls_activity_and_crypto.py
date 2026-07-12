"""Enable per-user RLS on caregiver_activity_log + user_encryption_keys (WS1 2e)

Revision ID: 028
Revises: 027

Two standard member-scoped tables:
- caregiver_activity_log.user_id is the MEMBER/subject (trusted_contact_id is the
  caregiver/actor), so the flat user_id policy is correct. Table is currently
  dormant (no writers) — RLS is safe now, and future writers are WITH-CHECK-fenced
  to user_id = the acting member's GUC.
- user_encryption_keys PK is user_id; field_crypto reads/creates the row only for
  the target user, in contexts where that member's GUC is set (member API /
  per-user workers / the document pipeline entrypoint) or under maintenance
  bypass (admin/cross-user). Flat user_id policy fits.
"""

from alembic import op
from app.db.rls import drop_isolation_statements, tenant_isolation_statements

revision = "028"
down_revision = "027"

_TABLES = ("caregiver_activity_log", "user_encryption_keys")


def upgrade() -> None:
    for table in _TABLES:
        for stmt in tenant_isolation_statements(table):
            op.execute(stmt)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for stmt in drop_isolation_statements(table):
            op.execute(stmt)
