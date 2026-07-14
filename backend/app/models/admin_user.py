import uuid
from datetime import datetime

from sqlalchemy import Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Stable Authentik OIDC subject, lazy-backfilled at BFF login (auth_authentik.py,
    # active only when auth_provider == "authentik"). Lets an admin session — which
    # stores only the opaque sub in Redis, no PII — resolve to the admin row without a
    # `users` row (admins are not members). UNIQUE like users.external_subject_id: one
    # admin ↔ one row ↔ one subject.
    external_subject_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="viewer")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
