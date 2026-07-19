"""Clear pre-encryption chat transcripts before content field-encryption

Revision ID: 044
Revises: 043

``chat_messages.content`` becomes per-user envelope-encrypted (``f2:`` tagged,
same scheme as RAG chunk_text / OCR text / document extracted_fields). The
read path (``field_crypto.decrypt_for_user``) is FAIL-CLOSED in prod: it
refuses to return an untagged value, so any legacy PLAINTEXT row written before
this change would raise on read instead of leaking. Those legacy rows must go.

WHY DELETE (not re-encrypt backfill)
------------------------------------
Prod holds ONLY disposable smoke-test conversations (clean-slate, no real PHI —
see CLAUDE.md: 1 smoketest member + 1 admin). Deleting them is the simplest
*correct* option and, critically, avoids running the async, OpenBao-Transit-
dependent envelope-encryption path inside an Alembic migration (the KEK lives in
OpenBao Transit; a backfill would need a live async app session + Transit
reachability at migrate time — fragile, and unnecessary for throwaway data).
Deleting also GUARANTEES no legacy plaintext row survives to trip the
fail-closed decrypt guard (no silent fail-open, no weakened guarantee).

chat_messages FKs chat_sessions ``ON DELETE CASCADE``; we delete children first
explicitly so the intent is unambiguous regardless of cascade.

Reversibility: this is a data-only cleanup with NO schema change. Deleted
throwaway transcripts cannot be (and need not be) restored, so ``downgrade`` is
a no-op — there is nothing schema-wise to reverse.
"""

from alembic import op

revision = "044"
down_revision = "043"


def upgrade() -> None:
    op.execute("DELETE FROM chat_messages")
    op.execute("DELETE FROM chat_sessions")


def downgrade() -> None:
    # Data-only cleanup of disposable pre-encryption test transcripts; there is
    # no schema change to reverse and deleted rows are not restorable.
    pass
