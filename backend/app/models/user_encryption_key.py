import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UserEncryptionKey(Base):
    """Per-tenant wrapped data-encryption key (envelope encryption).

    One row per user. ``wrapped_dek`` is the user's random 32-byte DEK sealed
    under the KEK named by ``kek_id`` (AES-256-GCM, user_id bound as AAD). See
    app.services.field_crypto for the scheme.
    """

    __tablename__ = "user_encryption_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    user = relationship("User")
