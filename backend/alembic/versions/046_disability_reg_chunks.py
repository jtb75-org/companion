"""Add disability_reg_chunks table for public federal regulations with pgvector support

Revision ID: 046
Revises: 045
"""

from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"


def _has_pgvector(connection) -> bool:
    """Check if pgvector extension is available."""
    result = connection.execute(
        sa.text(
            "SELECT 1 FROM pg_available_extensions "
            "WHERE name = 'vector'"
        )
    )
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()
    pgvector = _has_pgvector(conn)

    if pgvector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "disability_reg_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=50), nullable=False, server_default="US_Federal"),
        sa.Column("source_corpus", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=20), nullable=True),
        sa.Column("part", sa.String(length=20), nullable=True),
        sa.Column("section", sa.String(length=20), nullable=True),
        sa.Column("program", sa.String(length=20), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieval_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    if pgvector:
        op.execute(
            "ALTER TABLE disability_reg_chunks "
            "ADD COLUMN embedding vector(768)"
        )

    op.create_index(
        "ix_disability_reg_chunks_program",
        "disability_reg_chunks",
        ["program"],
    )
    op.create_index(
        "ix_disability_reg_chunks_citation",
        "disability_reg_chunks",
        ["citation"],
    )

    if pgvector:
        op.execute(
            "CREATE INDEX ix_disability_reg_chunks_embedding_hnsw "
            "ON disability_reg_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_disability_reg_chunks_embedding_hnsw")
    op.drop_index("ix_disability_reg_chunks_citation")
    op.drop_index("ix_disability_reg_chunks_program")
    op.drop_table("disability_reg_chunks")
