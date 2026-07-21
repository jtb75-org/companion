"""Add a pg_search BM25 index on disability_reg_chunks for HYBRID retrieval.

Revision ID: 047
Revises: 046

Pure lexical BM25 leg for hybrid (BM25 + pgvector, fused with RRF) retrieval over
the PUBLIC federal-regulation corpus. See app/services/knowledge_service.py.

The prod DB is ParadeDB (paradedb/paradedb:17), which bundles pg_search alongside
pgvector. This migration installs pg_search (if available) and builds a BM25 index
over the searchable text of each regulation chunk. It is a PUBLIC, non-PHI table
with no RLS, so the index is plain DDL with no tenant/isolation concerns.

Resilience: like migration 046 (pgvector), this is a no-op when pg_search is not
available in the connected database (e.g. a plain-Postgres CI/test DB with no
paradedb). The runtime hybrid path (knowledge_service.search_regulations) degrades
gracefully to vector-only when the BM25 index/extension is absent, so a DB without
this index still answers — just without the lexical leg.

pg_search version at authoring time: 0.23.1 (default_version in the deployed
ParadeDB image). The BM25 DDL uses the ``USING bm25 (...) WITH (key_field=...)``
form that has been stable since the 0.15 line: the index lists the key field plus
every column that should be searchable, and ``key_field`` names the unique key
(the UUID primary key ``id`` here).
"""

import sqlalchemy as sa

from alembic import op

revision = "047"
down_revision = "046"


_BM25_INDEX = "ix_disability_reg_chunks_bm25"


def _has_pg_search(connection) -> bool:
    """True if the pg_search extension is available to install in this database."""
    result = connection.execute(
        sa.text(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search'"
        )
    )
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_pg_search(conn):
        # Plain Postgres (CI/test) — nothing to build. The hybrid retrieval path
        # detects the missing extension/index and falls back to vector-only.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")

    # BM25 index over the two searchable columns. ``id`` (the UUID PK) is the
    # key_field pg_search uses to identify rows for scoring (paradedb.score(id)) and
    # for the @@@ predicate. text_content carries the regulation body (matches
    # "five-step", "appeal", etc.); citation carries "20 CFR § 404.1520" so a
    # citation-style query lands on the exact section.
    op.execute(
        f"CREATE INDEX {_BM25_INDEX} ON disability_reg_chunks "
        "USING bm25 (id, text_content, citation) "
        "WITH (key_field='id')"
    )


def downgrade() -> None:
    # Drop the index only; leave the extension in place (it is shared, idempotent to
    # re-create, and used by nothing else that this migration owns).
    op.execute(f"DROP INDEX IF EXISTS {_BM25_INDEX}")
