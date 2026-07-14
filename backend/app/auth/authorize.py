"""Authorization — determines what role a Firebase-authenticated user has.

After Firebase verifies identity (authentication), this module checks
what access the user has (authorization) by looking up their email in:
1. admin_users table → admin role (viewer/editor/admin)
2. trusted_contacts table → caregiver role (tier 1/2/3)
3. Neither → unauthorized (access denied)
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import maintenance_session
from app.models.admin_user import AdminUser
from app.models.trusted_contact import TrustedContact

logger = logging.getLogger(__name__)


async def authorized_caregiver_contact_id(email: str, user_id) -> uuid.UUID | None:
    """Return the ACTIVE ``trusted_contacts.id`` for (email, member), else ``None``.

    This is the access gate PLUS the identity needed to write the append-only
    ``caregiver_activity_log`` (which requires ``trusted_contact_id``). Narrow
    (email, user_id, is_active) lookup on the maintenance (BYPASSRLS) session —
    caregiver auth runs before any member GUC, so a normal app-role read fails
    closed; it reveals only whether this one pair is an active relationship.
    """
    if not email:
        return None
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(TrustedContact.id).where(
                TrustedContact.contact_email == email,
                TrustedContact.user_id == user_id,
                TrustedContact.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()


async def caregiver_authorized_for_member(email: str, user_id) -> bool:
    """True if ``email`` is an active trusted contact for member ``user_id``.

    Thin bool wrapper over ``authorized_caregiver_contact_id`` for the endpoints
    that only gate (activity/alerts) and don't need the contact id.
    """
    return await authorized_caregiver_contact_id(email, user_id) is not None


@dataclass
class AuthorizedUser:
    """Result of authorization check."""
    email: str
    role: str  # "admin", "caregiver", "unauthorized"
    admin_user: AdminUser | None = None
    admin_role: str | None = None  # viewer, editor, admin
    caregiver_contacts: list = None  # list of TrustedContact

    def __post_init__(self):
        if self.caregiver_contacts is None:
            self.caregiver_contacts = []

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_caregiver(self) -> bool:
        return self.role == "caregiver"

    @property
    def is_authorized(self) -> bool:
        return self.role != "unauthorized"


async def authorize_by_email(
    db: AsyncSession, email: str
) -> AuthorizedUser:
    """Look up what role an authenticated email has.

    Checks admin_users first, then trusted_contacts.
    Returns AuthorizedUser with role and details.
    """
    # Check admin_users table
    result = await db.execute(
        select(AdminUser).where(
            AdminUser.email == email,
            AdminUser.is_active.is_(True),
        )
    )
    admin = result.scalar_one_or_none()
    if admin:
        logger.info(f"Authorized as admin: {email} ({admin.role})")
        return AuthorizedUser(
            email=email,
            role="admin",
            admin_user=admin,
            admin_role=admin.role,
        )

    # Check trusted_contacts table. This by-email lookup runs before any member
    # GUC is set (we're resolving the caller's role), so under trusted_contacts
    # RLS it must run on the maintenance (BYPASSRLS) session or it fails closed
    # to 0 rows and every caregiver is misclassified as unauthorized. email is
    # the caregiver-auth index; matching rows are exactly this caller's active
    # relationships across the members who invited them.
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(TrustedContact).where(
                TrustedContact.contact_email == email,
                TrustedContact.is_active.is_(True),
            )
        )
        contacts = result.scalars().all()
    if contacts:
        logger.info(
            f"Authorized as caregiver: {email} "
            f"({len(contacts)} user(s))"
        )
        return AuthorizedUser(
            email=email,
            role="caregiver",
            caregiver_contacts=list(contacts),
        )

    # Not found in either table
    logger.warning(f"Unauthorized access attempt: {email}")
    return AuthorizedUser(email=email, role="unauthorized")
