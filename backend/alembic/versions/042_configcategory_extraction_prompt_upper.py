"""Rename configcategory value 'extraction_prompt' -> 'EXTRACTION_PROMPT'

Revision ID: 042
Revises: 041

SQLAlchemy maps the ConfigCategory StrEnum by its member NAME (UPPERCASE) as the
persisted DB value — that is why every other category works: the type
`configcategory` holds 'SUMMARIZATION_PROMPT', 'DD_PERSONA',
'PIPELINE_THRESHOLD', etc. (all UPPERCASE). The extraction_prompt value was the
lone exception, stored lowercase as 'extraction_prompt'.

Consequence: extraction.py:_get_extraction_prompt (and the admin Prompts UI
read/write path) sends 'EXTRACTION_PROMPT', which the enum type does not contain,
so Postgres raises InvalidTextRepresentationError. The except-block swallows it
and every document silently falls back to the default prompt ("Failed to load
extraction prompt for <type>, using default" on EVERY doc).

Fix: rename the enum label. This is a metadata-only catalog change (no table
rewrite). The system_config table has ZERO rows for this category today, so there
is no data to migrate.

ALTER TYPE ... RENAME VALUE requires PG 10+ and cannot run inside a transaction
block on some versions, so use the autocommit block (matching migration 032's
pattern for enum edits).

Verify afterwards:
    SELECT enum_range(NULL::configcategory);
should list 'EXTRACTION_PROMPT' (uppercase) and no lowercase 'extraction_prompt'.
"""

from alembic import op

revision = "042"
down_revision = "041"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE configcategory "
            "RENAME VALUE 'extraction_prompt' TO 'EXTRACTION_PROMPT'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE configcategory "
            "RENAME VALUE 'EXTRACTION_PROMPT' TO 'extraction_prompt'"
        )
