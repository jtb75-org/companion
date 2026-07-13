"""Add documents.possible_duplicate_of (fuzzy near-duplicate hint)

Revision ID: 034
Revises: 033

Set by the pipeline when a document's extracted fields closely match an earlier
document of the same member (a likely re-photograph of the same document).
Non-destructive: a hint the app surfaces to the member, never an auto-merge.
Self-referential FK to documents.id, ON DELETE SET NULL so deleting the earlier
document just clears the hint.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "034"
down_revision = "033"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("possible_duplicate_of", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_possible_duplicate_of",
        "documents",
        "documents",
        ["possible_duplicate_of"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_possible_duplicate_of", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "possible_duplicate_of")
