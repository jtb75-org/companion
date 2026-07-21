import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RegIngestionRun(Base):
    """One row per regulation-ingestion worker run (per source).

    Public/operational audit — NO tenant RLS, NO PHI (the reg corpus is public
    federal data). Records the reconcile counts + terminal status so the admin
    console and freshness alerts can answer "when did this source last refresh,
    and did it succeed?".
    """

    __tablename__ = "reg_ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ``source`` mirrors ``disability_reg_chunks.source_corpus`` (e.g. "eCFR",
    # "Federal_Register"). ``mode`` is "incremental" or "reconcile".
    source: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "success" | "failed" | "aborted_fetch" | "aborted_embed" | "aborted_purge".
    status: Mapped[str] = mapped_column(Text, nullable=False)
    docs_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    docs_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    docs_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    docs_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    docs_purged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embed_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
