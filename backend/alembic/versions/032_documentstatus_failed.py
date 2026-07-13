"""Add 'failed' to the documentstatus enum

Revision ID: 032
Revises: 031

The admin "cancel document" endpoint sets DocumentStatus.FAILED, and the
pipeline can mark a document failed on error, but the Postgres enum type
`documentstatus` never had that value — so the write 500s. Add it.

The label is the uppercase member NAME 'FAILED', not the value 'failed':
SQLAlchemy's mapped Enum persists Python enum member names, and every existing
documentstatus label is uppercase (RECEIVED, PENDING_REVIEW, …). Adding the
lowercase value would leave the app writing 'FAILED' against a type that lacks it.

ALTER TYPE ... ADD VALUE cannot run inside a transaction on older PG; it is safe
from PG 12+ as long as the new value isn't used in the same transaction (it
isn't here). IF NOT EXISTS makes it idempotent.
"""

from alembic import op

revision = "032"
down_revision = "031"


def upgrade() -> None:
    # Run outside the migration's transaction — ALTER TYPE ADD VALUE is not
    # transactional on some PG versions.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE documentstatus ADD VALUE IF NOT EXISTS 'FAILED'")


def downgrade() -> None:
    # Postgres cannot DROP a value from an enum type; leaving 'failed' in place
    # is harmless (no rows reference it after a downgrade of the app).
    pass
