"""Per-tenant envelope encryption: DEK keyring table + profile PII to Text.

Adds ``user_encryption_keys`` (one wrapped DEK per user) and converts the
encrypted profile columns ``users.date_of_birth`` (Date) and ``users.address``
(JSONB) to Text so they can hold tagged ciphertext. The other encrypted columns
(documents.extracted_fields/spoken_summary/card_summary,
pending_reviews.proposed_record_data, functional_memory.value) are already Text
(see migration 017 + the EncryptedText impl) and need no DDL change.

Production DB is empty of PHI, so the type conversions carry no real data; the
``USING`` casts are retained so the migration is correct on non-empty DBs too.

Revision ID: 021
Revises: 020
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_encryption_keys",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Profile PII columns -> Text (hold per-tenant envelope ciphertext).
    op.alter_column(
        "users",
        "date_of_birth",
        type_=sa.Text(),
        existing_type=sa.Date(),
        postgresql_using="date_of_birth::text",
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "address",
        type_=sa.Text(),
        existing_type=JSONB(),
        postgresql_using="address::text",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "address",
        type_=JSONB(),
        existing_type=sa.Text(),
        postgresql_using="address::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "date_of_birth",
        type_=sa.Date(),
        existing_type=sa.Text(),
        postgresql_using="date_of_birth::date",
        existing_nullable=True,
    )
    op.drop_table("user_encryption_keys")
