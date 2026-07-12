"""Service layer for caregiver invitations (Part 1 — getting people on the platform)."""

import secrets
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import maintenance_session
from app.models.enums import AccountStatus, InvitationStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User

INVITATION_TTL_DAYS = 14


def generate_invitation_token() -> str:
    return secrets.token_urlsafe(36)


async def get_or_create_stub_user(
    db: AsyncSession, email: str, name: str
) -> tuple[User, bool]:
    """Find or create a stub user (account_status='invited') for ``email``.

    Creating/reading another person's account by email is inherently
    cross-tenant, so this runs on the maintenance (BYPASSRLS) session: under
    per-user RLS a member's own session can neither see nor INSERT another
    user's row (WITH CHECK would reject the stub whose id != the inviter's GUC).
    ``db`` is kept for signature compatibility but not used for the user row.
    Returns (user, created); the returned user is detached (loaded scalars are
    safe — callers use existence / .id only).
    """
    async with maintenance_session() as mdb:
        result = await mdb.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            return user, False

        user = User(
            email=email,
            preferred_name=name,
            display_name=name,
            account_status=AccountStatus.INVITED,
        )
        mdb.add(user)
        await mdb.flush()
        await mdb.commit()
        return user, True


async def create_member_invitation(
    db: AsyncSession,
    inviter_user_id: UUID,
    email: str,
    contact_name: str,
    relationship_type: str,
    access_tier: str = "tier_1",
) -> TrustedContact:
    """Member invites a caregiver — creates TrustedContact immediately."""
    # Check for existing contact for this member with this email
    result = await db.execute(
        select(TrustedContact).where(
            TrustedContact.user_id == inviter_user_id,
            TrustedContact.contact_email == email,
        )
    )
    existing = result.scalar_one_or_none()

    now = datetime.utcnow()
    token = generate_invitation_token()

    if existing:
        # Re-invite: reset status, generate new token
        existing.invitation_status = InvitationStatus.PENDING
        existing.invitation_token = token
        existing.invited_at = now
        existing.invited_by_user_id = inviter_user_id
        existing.accepted_at = None
        existing.is_active = False
        await db.flush()
        # Ensure stub user exists
        await get_or_create_stub_user(db, email, contact_name)
        return existing

    # Ensure stub user exists
    await get_or_create_stub_user(db, email, contact_name)

    contact = TrustedContact(
        user_id=inviter_user_id,
        contact_name=contact_name,
        contact_email=email,
        relationship_type=relationship_type,
        access_tier=access_tier,
        is_active=False,  # Not active until accepted
        invitation_status=InvitationStatus.PENDING,
        invitation_token=token,
        invited_at=now,
        invited_by_user_id=inviter_user_id,
    )
    db.add(contact)
    await db.flush()
    return contact


async def create_admin_platform_invitation(
    db: AsyncSession,
    admin_id: UUID,
    email: str,
    name: str,
) -> tuple[User, bool]:
    """Admin invites someone to the platform (Part 1 only, no member assignment)."""
    return await get_or_create_stub_user(db, email, name)


async def get_invitation_by_token(
    db: AsyncSession, token: str
) -> TrustedContact | None:
    """Look up an invitation by token. Returns None if not found or expired."""
    result = await db.execute(
        select(TrustedContact).where(TrustedContact.invitation_token == token)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return None

    # Check expiry
    if contact.invited_at:
        invited_at = contact.invited_at
        if invited_at.tzinfo:
            invited_at = invited_at.replace(tzinfo=None)
        expires = invited_at + timedelta(days=INVITATION_TTL_DAYS)
        if datetime.utcnow() > expires:
            contact.invitation_status = InvitationStatus.EXPIRED
            await db.flush()
            return None

    return contact


async def accept_invitation(
    db: AsyncSession, token: str, accepting_email: str
) -> TrustedContact | None:
    """Accept an invitation. Returns the TrustedContact or None if invalid."""
    contact = await get_invitation_by_token(db, token)
    if contact is None:
        return None

    if contact.invitation_status != InvitationStatus.PENDING:
        return None

    if contact.contact_email and contact.contact_email.lower() != accepting_email.lower():
        return None

    now = datetime.utcnow()
    contact.invitation_status = InvitationStatus.ACCEPTED
    contact.accepted_at = now
    contact.is_active = True
    contact.invitation_token = None  # Consume the token

    # Activate the accepting caregiver's stub account. Cross-tenant (the
    # caregiver has no member GUC on this session), so run it on the maintenance
    # (BYPASSRLS) session — under users RLS the by-email read + INVITED->ACTIVE
    # write would otherwise fail-close.
    async with maintenance_session() as mdb:
        u = (
            await mdb.execute(
                select(User).where(User.email == accepting_email)
            )
        ).scalar_one_or_none()
        if u and u.account_status == AccountStatus.INVITED:
            u.account_status = AccountStatus.ACTIVE
            await mdb.commit()

    await db.flush()

    # Notify the member who sent the invitation
    from app.services.push_notification_service import (
        notify_caregiver_status_change,
    )

    await notify_caregiver_status_change(
        db,
        inviter_user_id=contact.user_id,
        caregiver_name=contact.contact_name,
        new_status="accepted",
    )

    return contact


async def decline_invitation(
    db: AsyncSession, token: str, declining_email: str
) -> TrustedContact | None:
    """Decline an invitation."""
    contact = await get_invitation_by_token(db, token)
    if contact is None:
        return None

    if contact.invitation_status != InvitationStatus.PENDING:
        return None

    if contact.contact_email and contact.contact_email.lower() != declining_email.lower():
        return None

    contact.invitation_status = InvitationStatus.DECLINED
    contact.is_active = False
    contact.invitation_token = None
    await db.flush()

    # Notify the member who sent the invitation
    from app.services.push_notification_service import (
        notify_caregiver_status_change,
    )

    await notify_caregiver_status_change(
        db,
        inviter_user_id=contact.user_id,
        caregiver_name=contact.contact_name,
        new_status="declined",
    )

    return contact
