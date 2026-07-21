"""Add a pg_search BM25 index on disability_reg_chunks for HYBRID retrieval.

Revision ID: 047
Revises: 046

Pure lexical BM25 leg for hybrid (BM25 + pgvector, fused with RRF) retrieval over
the PUBLIC federal-regulation corpus. See app/services/knowledge_service.py.

The prod DB is ParadeDB (paradedb/paradedb:17), which bundles pg_search alongside
pgvector. This migration builds a BM25 index over the searchable text of each
regulation chunk. It is a PUBLIC, non-PHI table with no RLS, so the index is plain
DDL with no tenant/isolation concerns.

The pg_search EXTENSION is NOT created here. Creating it requires a superuser
(``CREATE EXTENSION pg_search`` errors with "must be superuser to create a base
type"), but this migration runs as the non-superuser app owner ``companion``.
Instead the extension is created once, as the postgres superuser, at CNPG cluster
bootstrap via ``postInitApplicationSQL`` (companion-gitops db-cluster.yaml),
mirroring how ``vector`` is provisioned. This migration only builds the INDEX,
which the owner CAN do once the extension is present.

Resilience: like migration 046 (pgvector), this is a no-op when pg_search is not
installed in the connected database (e.g. a plain-Postgres CI/test DB with no
paradedb, or before the bootstrap extension exists). We probe ``pg_extension`` for
the INSTALLED extension (not ``pg_available_extensions``, since we no longer try to
install it) and skip the index if absent. The runtime hybrid path
(knowledge_service.search_regulations) degrades gracefully to vector-only when the
BM25 index/extension is absent, so a DB without this index still answers — just
without the lexical leg.

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
    """True if the pg_search extension is already INSTALLED in this database.

    Checks ``pg_extension`` (installed), NOT ``pg_available_extensions``
    (installable): this migration no longer creates the extension (that needs a
    superuser — see module docstring), so it only builds the index when the
    superuser-provisioned extension is actually present. Absent on plain-Postgres
    CI → the index is skipped and hybrid retrieval degrades to vector-only.
    """
    result = connection.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
    )
    return result.scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_pg_search(conn):
        # No pg_search extension installed (plain-Postgres CI/test, or a cluster
        # not yet bootstrapped with the extension). Nothing to build; the hybrid
        # retrieval path detects the missing index and falls back to vector-only.
        return

    # BM25 index over the two searchable columns. ``id`` (the UUID PK) is the
    # key_field pg_search uses to identify rows for scoring (paradedb.score(id)) and
    # for the @@@ predicate. text_content carries the regulation body (matches
    # "five-step", "appeal", etc.); citation carries "20 CFR § 404.1520" so a
    # citation-style query lands on the exact section.
    #
    # IF NOT EXISTS keeps this idempotent: 047 is already applied in prod (extension
    # + index built via manual superuser remediation), so re-running is a clean
    # no-op there and correct on a fresh DB where the extension was pre-created at
    # bootstrap.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_BM25_INDEX} ON disability_reg_chunks "
        "USING bm25 (id, text_content, citation) "
        "WITH (key_field='id')"
    )


def downgrade() -> None:
    # Drop the index only; leave the extension in place (it is shared, idempotent to
    # re-create, and used by nothing else that this migration owns).
    op.execute(f"DROP INDEX IF EXISTS {_BM25_INDEX}")
