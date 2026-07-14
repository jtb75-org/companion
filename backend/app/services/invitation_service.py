"""Service layer for caregiver invitations (Part 1 — getting people on the platform)."""

import secrets
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.context import set_user_context
from app.db.session import maintenance_session
from app.integrations.authentik_admin import provision_authentik_account
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
            created = False
        else:
            user = User(
                email=email,
                preferred_name=name,
                display_name=name,
                account_status=AccountStatus.INVITED,
            )
            mdb.add(user)
            await mdb.flush()
            await mdb.commit()
            created = True

    # Provision the matching Authentik account (branded BFF provisioning, PR 1).
    # Gated on the master switch here so the Firebase default stays byte-identical;
    # the call is itself best-effort + idempotent (no-op if the account exists, or
    # if Authentik is unreachable), so it can neither fail nor roll back the stub.
    # Runs after the commit / outside the maintenance session — it is HTTP-only.
    if settings.authentik_enabled:
        await provision_authentik_account(email, name)
    return user, created


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


async def _load_active_invitation(
    session: AsyncSession, token: str
) -> TrustedContact | None:
    """Load an invitation by token on ``session``, flipping it to EXPIRED (in the
    session, for the caller to commit) if past TTL. Returns the still-attached
    contact, or None if not found or expired."""
    result = await session.execute(
        select(TrustedContact).where(TrustedContact.invitation_token == token)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return None

    if contact.invited_at:
        invited_at = contact.invited_at
        if invited_at.tzinfo:
            invited_at = invited_at.replace(tzinfo=None)
        expires = invited_at + timedelta(days=INVITATION_TTL_DAYS)
        if datetime.utcnow() > expires:
            contact.invitation_status = InvitationStatus.EXPIRED
            return None

    return contact


async def get_invitation_by_token(
    db: AsyncSession, token: str
) -> TrustedContact | None:
    """Look up an invitation by token. Returns None if not found or expired.

    The token is a single-use, high-entropy capability and this lookup happens
    before any member GUC exists (public /validate + accept/decline), so it runs
    on the maintenance (BYPASSRLS) session — under trusted_contacts RLS a normal
    session would fail-close to 0 rows. The returned contact is detached; callers
    read scalar attrs only (sessions are expire_on_commit=False)."""
    async with maintenance_session() as mdb:
        contact = await _load_active_invitation(mdb, token)
        await mdb.commit()  # persist a possible EXPIRED flip
        return contact


async def accept_invitation(
    db: AsyncSession, token: str, accepting_email: str
) -> TrustedContact | None:
    """Accept an invitation. Returns the TrustedContact or None if invalid."""
    # The whole token lookup → contact mutation → stub activation is cross-tenant
    # (the caregiver has no member GUC), so run it on ONE maintenance (BYPASSRLS)
    # session. WRITES to trusted_contacts by a caregiver session would otherwise
    # be rejected by WITH CHECK (user_id != GUC); the token is the capability.
    async with maintenance_session() as mdb:
        contact = await _load_active_invitation(mdb, token)
        if contact is None:
            await mdb.commit()  # persist a possible EXPIRED flip
            return None
        if contact.invitation_status != InvitationStatus.PENDING:
            return None
        if (
            contact.contact_email
            and contact.contact_email.lower() != accepting_email.lower()
        ):
            return None

        contact.invitation_status = InvitationStatus.ACCEPTED
        contact.accepted_at = datetime.utcnow()
        contact.is_active = True
        contact.invitation_token = None  # Consume the token

        # Activate the accepting caregiver's stub account on the same bypass
        # session (under users RLS the by-email read + INVITED->ACTIVE write
        # would otherwise fail-close).
        u = (
            await mdb.execute(select(User).where(User.email == accepting_email))
        ).scalar_one_or_none()
        if u and u.account_status == AccountStatus.INVITED:
            u.account_status = AccountStatus.ACTIVE

        member_user_id = contact.user_id
        caregiver_name = contact.contact_name
        await mdb.commit()

    # Notify the member who sent the invitation. send_push reads the member's
    # device_tokens (under RLS), so scope the request session to the member.
    await set_user_context(db, member_user_id)
    from app.services.push_notification_service import (
        notify_caregiver_status_change,
    )

    await notify_caregiver_status_change(
        db,
        inviter_user_id=member_user_id,
        caregiver_name=caregiver_name,
        new_status="accepted",
    )

    return contact


async def decline_invitation(
    db: AsyncSession, token: str, declining_email: str
) -> TrustedContact | None:
    """Decline an invitation."""
    async with maintenance_session() as mdb:
        contact = await _load_active_invitation(mdb, token)
        if contact is None:
            await mdb.commit()  # persist a possible EXPIRED flip
            return None
        if contact.invitation_status != InvitationStatus.PENDING:
            return None
        if (
            contact.contact_email
            and contact.contact_email.lower() != declining_email.lower()
        ):
            return None

        contact.invitation_status = InvitationStatus.DECLINED
        contact.is_active = False
        contact.invitation_token = None

        member_user_id = contact.user_id
        caregiver_name = contact.contact_name
        await mdb.commit()

    # Notify the member who sent the invitation (send_push reads the member's
    # device_tokens under RLS → scope the request session to the member).
    await set_user_context(db, member_user_id)
    from app.services.push_notification_service import (
        notify_caregiver_status_change,
    )

    await notify_caregiver_status_change(
        db,
        inviter_user_id=member_user_id,
        caregiver_name=caregiver_name,
        new_status="declined",
    )

    return contact
