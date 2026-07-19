"""Charges endpoint — returns users assigned to the current admin/caregiver."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import resolve_caregiver_session
from app.config import settings
from app.db.session import get_maintenance_db
from app.models.trusted_contact import TrustedContact
from app.models.user import User

router = APIRouter(tags=["Auth"])


@router.get("/api/v1/auth/my-charges")
async def get_my_charges(
    request: Request,
    db: AsyncSession = Depends(get_maintenance_db),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get users (charges) assigned to the current user.

    Looks up the authenticated user's email in trusted_contacts
    and returns the list of users they're assigned to.
    """
    # Dev bypass
    if settings.dev_auth_bypass:
        if authorization is None:
            # Return all users in dev mode
            result = await db.execute(select(User))
            users = result.scalars().all()
            return {
                "charges": [
                    {
                        "user_id": str(u.id),
                        "name": u.preferred_name or u.display_name,
                        "email": u.email,
                    }
                    for u in users
                ]
            }

    # Resolve the caregiver's verified email from the Authentik BFF session.
    email = await resolve_caregiver_session(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Find trusted_contacts where this email is the contact
    result = await db.execute(
        select(TrustedContact, User).join(
            User, TrustedContact.user_id == User.id
        ).where(
            TrustedContact.contact_email == email,
            TrustedContact.is_active.is_(True),
        )
    )
    rows = result.all()

    charges = [
        {
            "user_id": str(contact.user_id),
            "name": user.preferred_name or user.display_name,
            "email": user.email,
            "access_tier": getattr(
                contact.access_tier, "value", str(contact.access_tier)
            ),
            "relationship": getattr(
                contact.relationship_type, "value",
                str(contact.relationship_type)
            ),
        }
        for contact, user in rows
    ]

    return {"charges": charges}
