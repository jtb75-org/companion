"""Add documents.content_fingerprint for exact-duplicate detection

Revision ID: 033
Revises: 032

SHA-256 (hex) of the uploaded page bytes. Lets the scan endpoint detect a member
re-uploading the identical file (the double-tap "I thought it glitched" case) and
return the existing document instead of creating a second copy. Indexed by
(user_id, content_fingerprint) for the per-user lookup (which RLS also scopes).
"""

import sqlalchemy as sa

from alembic import op

revision = "033"
down_revision = "032"


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_fingerprint", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_documents_user_fingerprint",
        "documents",
        ["user_id", "content_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_user_fingerprint", table_name="documents")
    op.drop_column("documents", "content_fingerprint")
