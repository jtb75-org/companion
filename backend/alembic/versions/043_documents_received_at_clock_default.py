"""documents.received_at default now() -> clock_timestamp()

Revision ID: 043
Revises: 042

``documents.received_at`` defaulted to ``now()``, which in PostgreSQL is
``transaction_timestamp()`` — CONSTANT for the entire transaction. Two documents
inserted in one transaction therefore received an IDENTICAL received_at down to
the microsecond, and any row created inside a reused/long-lived transaction
inherited that transaction's (potentially stale, past) start time rather than
its own ingest moment.

``clock_timestamp()`` is the actual wall clock, re-read per row, so each INSERT
gets an independent, accurate timestamp. This is DB-layer defense-in-depth; the
application (document_service.create_document) also now stamps received_at
explicitly per document, which is the primary fix.

Column stays ``timestamp without time zone`` (unchanged type) — this only
swaps the DEFAULT expression, so it is a metadata-only, reversible change.
"""

from alembic import op

revision = "043"
down_revision = "042"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents "
        "ALTER COLUMN received_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE documents ALTER COLUMN received_at SET DEFAULT now()"
    )
