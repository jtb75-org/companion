import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActivationToken(Base):
    """A single-use, email-keyed account-activation capability.

    Emitted alongside an account (an admin now, members later) so the person can set
    their Authentik password in Companion's branded UI and then log in. Deliberately
    NOT admin- or member-specific: it is keyed by ``email`` so any cohort reuses the
    same table and service.

    NOT per-member data — a token is consulted BEFORE any authenticated session /
    tenant GUC exists (public /activation/validate + set-password), so this table is
    accessed on a plain / maintenance (BYPASSRLS) session and is intentionally left
    OUT of per-user RLS (mirrors account_audit_log). The high-entropy ``token`` is the
    only capability; ``email`` is not unique (re-issuing supersedes a prior token).
    """

    __tablename__ = "activation_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Indexed but NOT unique: a re-issue for the same email inserts a fresh row and
    # supersedes any prior unused one (see activation_service.issue_activation_token).
    email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # UNIQUE + indexed: the token is the capability, looked up on every validate /
    # set-password. token_urlsafe(36) → ~48 bytes of entropy.
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    # Set to now() on consume (single-use) OR when superseded by a re-issue. NULL ⇒
    # still redeemable (subject to expiry).
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
