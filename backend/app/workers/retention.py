"""Retention worker — enforces document retention policy.

Runs nightly. Transitions documents through retention phases:
- Full → Important Only (raw text deleted)
- Important Only → Metadata Only (extracted fields deleted)
- Junk deleted at 30 days regardless

Logs all deletions to deletion_audit_log.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import maintenance_session
from app.models.audit import AccountAuditLog, DeletionAuditLog
from app.models.document import Document
from app.models.enums import (
    DeletionReason,
    DocumentClassification,
    RetentionPhase,
)

logger = logging.getLogger(__name__)

# Default retention windows (days)
FULL_RETENTION_DAYS = 30
IMPORTANT_RETENTION_DAYS = 90
JUNK_RETENTION_DAYS = 30

# How long to keep `signup_refused` audit rows. These hold non-user PII
# (the rejected email), so they are purged on a 90-day window. Rows for
# real members (e.g. `account_activated`) are retained.
SIGNUP_REFUSED_RETENTION_DAYS = 90
SIGNUP_REFUSED_EVENT = "signup_refused"


async def run_retention_worker():
    """Execute the full retention enforcement cycle.

    Retention is an inherently CROSS-USER maintenance sweep — it selects and
    mutates documents by classification/date across all members and purges
    signup-refused audit rows — so the whole cycle runs under the maintenance
    (BYPASSRLS) session (WS1 Phase 2c). Unlike the per-user workers there is no
    per-member scoping step; the operation legitimately spans everyone. Falls
    back to the normal session where the maintenance role is unconfigured
    (dev/test, no RLS).
    """
    async with maintenance_session() as db:
        try:
            junk_count = await _purge_junk(db)
            phase1_count = await _transition_full_to_important(db)
            phase2_count = await _transition_important_to_metadata(db)
            refused_count = await _purge_refused_signup_audit(db)

            await db.commit()

            logger.info(
                f"Retention worker complete: "
                f"junk_purged={junk_count} "
                f"full_to_important={phase1_count} "
                f"important_to_metadata={phase2_count} "
                f"signup_refused_purged={refused_count}"
            )
            return {
                "junk_purged": junk_count,
                "full_to_important": phase1_count,
                "important_to_metadata": phase2_count,
                "signup_refused_purged": refused_count,
            }
        except Exception:
            await db.rollback()
            logger.exception("Retention worker failed")
            raise


async def _purge_junk(db: AsyncSession) -> int:
    """Delete junk documents older than 30 days."""
    cutoff = datetime.utcnow() - timedelta(days=JUNK_RETENTION_DAYS)

    result = await db.execute(
        select(Document).where(
            Document.classification == DocumentClassification.JUNK,
            Document.received_at < cutoff,
        )
    )
    docs = result.scalars().all()

    for doc in docs:
        db.add(DeletionAuditLog(
            user_id=doc.user_id,
            entity_type="documents",
            entity_id=doc.id,
            reason=DeletionReason.RETENTION_POLICY,
        ))
        await db.delete(doc)

    await db.flush()
    return len(docs)


async def _purge_refused_signup_audit(db: AsyncSession) -> int:
    """Delete `signup_refused` audit rows older than the retention window.

    These rows hold a rejected email address (non-user PII) with no member
    row behind them, so they are not retained long-term. `account_activated`
    rows reference real members and are intentionally left untouched.
    """
    cutoff = datetime.utcnow() - timedelta(days=SIGNUP_REFUSED_RETENTION_DAYS)

    result = await db.execute(
        delete(AccountAuditLog).where(
            AccountAuditLog.event == SIGNUP_REFUSED_EVENT,
            AccountAuditLog.occurred_at < cutoff,
        )
    )
    return result.rowcount or 0


async def _transition_full_to_important(db: AsyncSession) -> int:
    """Move documents from full to important_only retention."""
    cutoff = datetime.utcnow() - timedelta(days=FULL_RETENTION_DAYS)

    result = await db.execute(
        select(Document).where(
            Document.retention_phase == RetentionPhase.FULL,
            Document.received_at < cutoff,
            Document.classification != DocumentClassification.JUNK,
        )
    )
    docs = result.scalars().all()

    for doc in docs:
        doc.retention_phase = RetentionPhase.IMPORTANT_ONLY
        doc.raw_text_ref = None  # Remove raw text reference

    await db.flush()
    return len(docs)


async def _transition_important_to_metadata(db: AsyncSession) -> int:
    """Strip documents to metadata only."""
    cutoff = datetime.utcnow() - timedelta(days=IMPORTANT_RETENTION_DAYS)

    result = await db.execute(
        select(Document).where(
            Document.retention_phase == RetentionPhase.IMPORTANT_ONLY,
            Document.received_at < cutoff,
        )
    )
    docs = result.scalars().all()

    for doc in docs:
        doc.retention_phase = RetentionPhase.METADATA_ONLY
        doc.extracted_fields = None
        doc.spoken_summary = None
        doc.card_summary = None
        doc.source_metadata = None

    await db.flush()
    return len(docs)
