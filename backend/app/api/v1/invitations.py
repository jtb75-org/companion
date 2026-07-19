"""App API — Invitation routes (member-initiated caregiver invitations)."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_authentik import _require_authentik_enabled
from app.api.v1.activation import _revoke_sessions_for_email
from app.auth.dependencies import User, require_complete_profile
from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db import get_db
from app.db.session import maintenance_session
from app.integrations.authentik_admin import (
    provision_authentik_account,
    set_authentik_password,
)
from app.integrations.email_service import (
    send_caregiver_invitation,
    send_invitation_accepted_notification,
)
from app.models.enums import AccountStatus
from app.models.user import User as UserModel
from app.schemas.invitation import (
    InvitationAccept,
    InvitationCreate,
    InvitationResponse,
    SetPasswordRequest,
)
from app.services import invitation_service
from app.services.password_policy import PasswordPolicyError, validate_password

log = logging.getLogger("companion.invitations")

router = APIRouter(prefix="/invitations", tags=["Invitations"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=InvitationResponse)
async def create_invitation(
    data: InvitationCreate,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Member invites a caregiver — creates TrustedContact + sends email."""
    contact = await invitation_service.create_member_invitation(
        db=db,
        inviter_user_id=user.id,
        email=data.email,
        contact_name=data.contact_name,
        relationship_type=data.relationship_type.value,
        access_tier=data.access_tier.value,
    )

    inviter_name = user.preferred_name or user.display_name
    email_sent = await send_caregiver_invitation(
        to_email=data.email,
        to_name=data.contact_name,
        user_name=inviter_name,
        relationship=data.relationship_type.value,
        invited_by=inviter_name,
        invitation_token=contact.invitation_token,
    )

    return InvitationResponse(
        contact_id=contact.id,
        invitation_status=contact.invitation_status,
        email_sent=email_sent,
    )


@router.post("/accept")
async def accept_invitation(
    data: InvitationAccept,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Caregiver (incl. a not-yet-active invitee) accepts an invitation by token.

    The web sends an ambient cookie session, so we resolve the session holder's
    IdP-verified email via ``resolve_caregiver_session``. An invitee resolves even before
    they are an active caregiver because ``create_member_invitation`` seeds an INVITED
    ``users`` stub for their email, which Authentik ``/auth/login`` admits and binds to
    their subject — ``_email_for_subject`` then recovers the email from that stub. The
    real authorization is still the token + ``contact_email`` match inside
    ``accept_invitation``."""
    email = await resolve_caregiver_session(request)
    if not email:
        raise HTTPException(401, "Not authenticated")

    contact = await invitation_service.accept_invitation(db, data.token, email)
    if contact is None:
        raise HTTPException(400, "Invalid, expired, or already-used invitation token")

    # Notify the member that their caregiver accepted. Reading the member's
    # user row is cross-tenant (the caregiver has no member GUC on this
    # session), so run it on the maintenance (BYPASSRLS) session — under users
    # RLS the by-id lookup would otherwise fail-close and blank the name.
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(UserModel).where(UserModel.id == contact.user_id)
        )
        member = result.scalar_one_or_none()
    if member:
        await send_invitation_accepted_notification(
            to_email=member.email,
            to_name=member.preferred_name or member.display_name,
            caregiver_name=contact.contact_name,
        )

    return {
        "accepted": True,
        "contact_id": str(contact.id),
        "member_name": member.preferred_name if member else None,
        "relationship_type": contact.relationship_type,
        "access_tier": contact.access_tier,
    }


@router.post("/decline")
async def decline_invitation(
    data: InvitationAccept,
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Caregiver (incl. a not-yet-active invitee) declines an invitation.

    Mirror of ``accept_invitation``: resolve the session holder's IdP-verified email via
    ``resolve_caregiver_session`` (an invitee resolves through their seeded INVITED
    ``users`` stub). The token + ``contact_email`` match inside ``decline_invitation``
    remains the real authorization."""
    email = await resolve_caregiver_session(request)
    if not email:
        raise HTTPException(401, "Not authenticated")

    contact = await invitation_service.decline_invitation(db, data.token, email)
    if contact is None:
        raise HTTPException(400, "Invalid, expired, or already-used invitation token")

    return {"declined": True}


@router.get("/validate")
async def validate_invitation_token(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Validate an invitation token (public, no auth). Used by the frontend landing page."""
    contact = await invitation_service.get_invitation_by_token(db, token)
    if contact is None:
        raise HTTPException(404, "Invalid or expired invitation")

    # Look up the member name AND the invitee's own users stub. This is a public,
    # unauthenticated endpoint with no member GUC, so both reads run on the
    # maintenance (BYPASSRLS) session — under users RLS the by-id/by-email lookups
    # would otherwise fail-close.
    async with maintenance_session() as mdb:
        member = (
            await mdb.execute(
                select(UserModel).where(UserModel.id == contact.user_id)
            )
        ).scalar_one_or_none()
        stub = (
            await mdb.execute(
                select(UserModel).where(UserModel.email == contact.contact_email)
            )
        ).scalar_one_or_none()

    # First-time setup ⇒ the invitee's stub is still INVITED (never activated ⇒ never
    # set a password). authentik_login_enabled is always True now (Authentik is the sole
    # auth path), so this is driven purely by the stub's INVITED status.
    needs_password_setup = (
        settings.authentik_login_enabled
        and stub is not None
        and stub.account_status == AccountStatus.INVITED
    )

    return {
        "valid": True,
        "contact_name": contact.contact_name,
        "contact_email": contact.contact_email,
        "member_name": member.preferred_name if member else None,
        "relationship_type": contact.relationship_type,
        "access_tier": contact.access_tier,
        "needs_password_setup": needs_password_setup,
    }


@router.post("/set-password")
async def set_invitation_password(data: SetPasswordRequest):
    """First-time invitee sets their Authentik password in Companion's branded UI.

    Authentik-only (404s under firebase). Does NOT log the invitee in or accept the
    invite — the web calls /auth/login + /invitations/accept next. Enforces
    first-time-only: only an INVITED stub may set a password here, so a leaked/reused
    invite token can't reset an already-established (ACTIVE) account's credentials."""
    _require_authentik_enabled()

    # The token is the capability; load it on the BYPASSRLS session (trusted_contacts
    # is per-member RLS-fenced and there is no member GUC on this public endpoint).
    async with maintenance_session() as mdb:
        contact = await invitation_service._load_active_invitation(mdb, data.token)
        await mdb.commit()  # persist a possible EXPIRED flip
    if contact is None:
        raise HTTPException(400, "Invalid, expired, or already-used invitation token")

    # Refuse unless the invitee's users stub is still INVITED (first-time only). A
    # missing stub or an ACTIVE one ⇒ an established account whose password must NOT
    # be resettable via an invite token.
    async with maintenance_session() as mdb:
        stub = (
            await mdb.execute(
                select(UserModel).where(UserModel.email == contact.contact_email)
            )
        ).scalar_one_or_none()
    if stub is None or stub.account_status != AccountStatus.INVITED:
        raise HTTPException(409, "This account is already set up. Please sign in.")

    # Strength-gate the password BEFORE touching the IdP. 422 (distinct from the 400
    # "invalid/expired token" the mobile client maps to invalid-link) so clients can
    # show the plain policy message. The rejected password is never echoed/logged.
    try:
        validate_password(
            data.password.get_secret_value(), email=contact.contact_email
        )
    except PasswordPolicyError as e:
        raise HTTPException(422, e.message) from None

    # Provision-ensure (idempotent — self-heals if PR 1 provisioning had failed),
    # then set the password. Provisioning is best-effort/never-raises; the password
    # set is must-succeed and its failure is surfaced as a clean 502.
    await provision_authentik_account(contact.contact_email, contact.contact_name)
    try:
        await set_authentik_password(
            contact.contact_email, data.password.get_secret_value()
        )
    except Exception:
        log.error(
            "failed to set Authentik password for invitee %s",
            contact.contact_email,
            exc_info=True,
        )
        raise HTTPException(
            502, "Could not set your password. Please try again."
        ) from None

    # Defense-in-depth: this is the SECOND path that sets a password, so it gets the same
    # revocation as /activation/set-password. Today it is a guaranteed no-op — the guard
    # above 409s unless the stub is still INVITED, and an INVITED stub has never had a
    # password, so it cannot have a live session. But that safety rests ENTIRELY on that
    # 409, which is a different invariant than activation's; if it is ever relaxed, this
    # path would silently start leaving live sessions behind a password change. One query
    # to make that impossible. Best-effort, like the activation hook.
    await _revoke_sessions_for_email(contact.contact_email)

    return {"ok": True, "email": contact.contact_email}
