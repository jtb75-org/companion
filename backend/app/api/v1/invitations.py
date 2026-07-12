"""App API — Invitation routes (member-initiated caregiver invitations)."""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import User, _extract_bearer_token, require_complete_profile
from app.db import get_db
from app.db.session import maintenance_session
from app.integrations.email_service import (
    send_caregiver_invitation,
    send_invitation_accepted_notification,
)
from app.models.user import User as UserModel
from app.schemas.invitation import InvitationAccept, InvitationCreate, InvitationResponse
from app.services import invitation_service

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
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Caregiver accepts an invitation by token. Requires Firebase auth."""
    decoded = await _extract_bearer_token(authorization)
    email = decoded.get("email")
    if not email:
        raise HTTPException(401, "Firebase token missing email claim")

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
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
):
    """Caregiver declines an invitation."""
    decoded = await _extract_bearer_token(authorization)
    email = decoded.get("email")
    if not email:
        raise HTTPException(401, "Firebase token missing email claim")

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

    # Look up the member name. This is a public, unauthenticated endpoint with
    # no member GUC, so read the member row on the maintenance (BYPASSRLS)
    # session — under users RLS the by-id lookup would otherwise fail-close.
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            select(UserModel).where(UserModel.id == contact.user_id)
        )
        member = result.scalar_one_or_none()

    return {
        "valid": True,
        "contact_name": contact.contact_name,
        "member_name": member.preferred_name if member else None,
        "relationship_type": contact.relationship_type,
        "access_tier": contact.access_tier,
    }
