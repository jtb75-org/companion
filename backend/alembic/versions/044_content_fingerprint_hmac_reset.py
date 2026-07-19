"""Reset documents.content_fingerprint for the per-user HMAC scheme

Revision ID: 044
Revises: 043

``content_fingerprint`` changed from an unsalted SHA-256 of the page bytes to a
per-member keyed HMAC-SHA-256 (see services/field_crypto.fingerprint_for_user),
so a privileged DB/maintenance breach can no longer correlate identical
documents across members or confirm a known document by guessing its hash.

Old SHA-256 values will never match the new HMACs, so we clear them. We do NOT
recompute in-place: the recompute needs BOTH the raw page bytes (in MinIO/object
storage, unreachable from a migration) and each member's OpenBao-wrapped DEK
(also out of reach here). ``content_fingerprint`` is a best-effort, non-
destructive exact-dedup cache — it is not referenced by any FK and losing it
only means a member could, in a brief window, upload an identical file twice
without the double-submit being collapsed. The cache self-heals: every new
upload writes the new HMAC, and the next identical re-upload dedupes normally.

Schema is unchanged (the column is already nullable Text), so this is a data-
only migration.
"""

from alembic import op
from app.db.rls_migration import rls_bypassed

revision = "044"
down_revision = "043"


def upgrade() -> None:
    # ``documents`` is under FORCE ROW LEVEL SECURITY, so this owner-run UPDATE
    # sets no ``app.current_user_id`` GUC and would match ZERO rows silently
    # (the original prod no-op — data since corrected manually via the BYPASSRLS
    # maintenance role). Wrap in rls_bypassed so any DB rebuilt-with-data applies
    # it correctly. On a fresh/empty DB this is a harmless no-op regardless.
    with rls_bypassed(op, "documents"):
        # Clear the stale SHA-256 fingerprints; the cache rebuilds as members
        # re-upload.
        op.execute("UPDATE documents SET content_fingerprint = NULL")


def downgrade() -> None:
    # Intentionally a no-op. There is no schema change to revert, and the old
    # plaintext SHA-256 values cannot (and must not) be reconstructed here:
    #   * the source page bytes live in MinIO, unreachable from a migration, and
    #   * restoring the unsalted SHA-256 would reintroduce the exact cross-member
    #     correlation weakness this revision closes.
    # The exact-dedup cache is best-effort and self-heals on re-upload in either
    # direction, so leaving fingerprints as-is on downgrade is safe.
    pass
