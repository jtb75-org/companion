"""Denormalize user_id onto chat_messages + medication_confirmations (WS1 Phase 2b)

Revision ID: 022
Revises: 021

These two tables have no direct user_id (only chat_session_id / medication_id),
so per-user RLS would need a per-row EXISTS-through-parent subquery on a hot path
(chat_messages especially). Per the Phase 2 design (D2), we denormalize a user_id
column so the standard flat `user_id = current_setting` policy applies, and keep
it honest with an enforce_same_user trigger (child.user_id must equal the parent's
user_id — mirrors HCC's enforce_same_account pattern).

Column is added NULLABLE here + backfilled from the parent. The write paths set it
going forward (deployed with this migration). NOT NULL is tightened in Phase 2e,
right before the policies land, once no NULLs remain — this keeps a rolling deploy
(old pods still inserting) from hitting a NOT NULL violation mid-roll.
"""

from alembic import op

revision = "022"
down_revision = "021"

# NULL-tolerant during the nullable transition: only validate populated user_ids.
_ENFORCE_SAME_USER_FN = """
CREATE OR REPLACE FUNCTION enforce_same_user() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
  fk_col text := TG_ARGV[0];
  ref_table text := TG_ARGV[1];
  fk_val uuid;
  ref_user uuid;
BEGIN
  IF NEW.user_id IS NULL THEN
    RETURN NEW;  -- transition tolerance; NOT NULL is enforced in Phase 2e
  END IF;
  EXECUTE format('SELECT ($1).%I', fk_col) INTO fk_val USING NEW;
  IF fk_val IS NULL THEN
    RETURN NEW;
  END IF;
  EXECUTE format('SELECT user_id FROM %I WHERE id = $1', ref_table)
    INTO ref_user USING fk_val;
  IF ref_user IS DISTINCT FROM NEW.user_id THEN
    RAISE EXCEPTION 'user_id mismatch on %.% (parent % has %, row has %)',
      TG_TABLE_NAME, fk_col, ref_table, ref_user, NEW.user_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$fn$
"""


def upgrade() -> None:
    for table, parent, fk in (
        ("chat_messages", "chat_sessions", "chat_session_id"),
        ("medication_confirmations", "medications", "medication_id"),
    ):
        op.execute(f"ALTER TABLE {table} ADD COLUMN user_id uuid")
        op.execute(
            f"UPDATE {table} c SET user_id = p.user_id "
            f"FROM {parent} p WHERE p.id = c.{fk}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_user_id "
            f"FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        )
        op.execute(f"CREATE INDEX ix_{table}_user_id ON {table} (user_id)")

    op.execute(_ENFORCE_SAME_USER_FN.strip())
    op.execute(
        "CREATE TRIGGER trg_chat_messages_same_user "
        "BEFORE INSERT OR UPDATE ON chat_messages "
        "FOR EACH ROW EXECUTE FUNCTION enforce_same_user('chat_session_id', 'chat_sessions')"
    )
    op.execute(
        "CREATE TRIGGER trg_medication_confirmations_same_user "
        "BEFORE INSERT OR UPDATE ON medication_confirmations "
        "FOR EACH ROW EXECUTE FUNCTION enforce_same_user('medication_id', 'medications')"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_medication_confirmations_same_user "
        "ON medication_confirmations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chat_messages_same_user ON chat_messages"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_same_user()")
    for table in ("chat_messages", "medication_confirmations"):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_user_id")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_user_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS user_id")
