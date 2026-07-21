"""Regulation ingestion reconcile tracking (Phase A).

Adds stable-identity + change-detection columns to the public
``disability_reg_chunks`` table and a new ``reg_ingestion_runs`` audit table so
the ingestion worker can reconcile (new/changed/unchanged/absent) instead of the
old full-delete-and-reinsert.

These are PUBLIC federal-regulation tables — NO tenant RLS, NO PHI — so the
FORCE-ROW-LEVEL-SECURITY silent-noop migration gotcha does not apply. This is
DDL only (no tenant DML) regardless.

Revision ID: 047
Revises: 046
"""

import sqlalchemy as sa

from alembic import op

revision = "047"
down_revision = "046"


def upgrade() -> None:
    # ── Stable identity + change detection on the chunk table ──────────────────
    # All additive + nullable so pre-existing rows (ingested before reconcile) are
    # untouched; the worker backfills source_id/content_hash/last_seen_at on the
    # next run per source.
    op.add_column(
        "disability_reg_chunks",
        sa.Column("source_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "disability_reg_chunks",
        sa.Column("content_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "disability_reg_chunks",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "disability_reg_chunks",
        sa.Column("ingestion_run_id", sa.UUID(), nullable=True),
    )

    # Reconcile loads the per-source index keyed by (source_corpus, source_id).
    op.create_index(
        "ix_disability_reg_chunks_corpus_source_id",
        "disability_reg_chunks",
        ["source_corpus", "source_id"],
    )

    # ── Run audit table (§10 of the spec) ──────────────────────────────────────
    op.create_table(
        "reg_ingestion_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("docs_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("docs_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("docs_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("docs_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("docs_purged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embed_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reg_ingestion_runs_source_started",
        "reg_ingestion_runs",
        ["source", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reg_ingestion_runs_source_started")
    op.drop_table("reg_ingestion_runs")
    op.drop_index("ix_disability_reg_chunks_corpus_source_id")
    op.drop_column("disability_reg_chunks", "ingestion_run_id")
    op.drop_column("disability_reg_chunks", "last_seen_at")
    op.drop_column("disability_reg_chunks", "content_hash")
    op.drop_column("disability_reg_chunks", "source_id")
