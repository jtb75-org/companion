"""Admin API — Trusted Contacts management across all users."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.models.enums import AccessTier, RelationshipType
from app.models.trusted_contact import TrustedContact
from app.models.user import User

_editor = require_admin_role("editor")

router = APIRouter(tags=["Admin - Contacts"])


@router.get("/admin/contacts")
async def list_all_contacts(
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """List all trusted contacts across all users."""
    result = await db.execute(
        select(TrustedContact, User)
        .join(User, TrustedContact.user_id == User.id)
        .order_by(User.preferred_name, TrustedContact.contact_name)
    )
    rows = result.all()
    return {
        "contacts": [
            {
                "id": str(contact.id),
                "user_id": str(contact.user_id),
                "user_name": user.preferred_name or user.display_name,
                "contact_name": contact.contact_name,
                "contact_email": contact.contact_email,
                "contact_phone": contact.contact_phone,
                "relationship_type": getattr(
                    contact.relationship_type, "value",
                    str(contact.relationship_type),
                ),
                "access_tier": getattr(contact.access_tier, "value", str(contact.access_tier)),
                "is_active": contact.is_active,
            }
            for contact, user in rows
        ],
        "total": len(rows),
    }


@router.get("/admin/users")
async def list_all_users(
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """List all users (for the user picker when assigning contacts)."""
    result = await db.execute(select(User).order_by(User.preferred_name))
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "name": u.preferred_name or u.display_name,
            }
            for u in users
        ]
    }


@router.post("/admin/contacts", status_code=status.HTTP_201_CREATED)
async def create_contact(
    data: dict,  # Simple dict for now
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Add a trusted contact for a user."""
    # Validate user exists
    user = await db.get(User, uuid.UUID(data["user_id"]))
    if not user:
        raise HTTPException(404, "User not found")

    contact = TrustedContact(
        user_id=user.id,
        contact_name=data["contact_name"],
        contact_email=data.get("contact_email"),
        contact_phone=data.get("contact_phone"),
        relationship_type=RelationshipType(data.get("relationship_type", "family")),
        access_tier=AccessTier(data.get("access_tier", "tier_1")),
    )
    db.add(contact)
    await db.flush()
    return {"id": str(contact.id), "created": True}


@router.delete("/admin/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: uuid.UUID,
    admin: AdminUser = Depends(_editor),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Remove a trusted contact."""
    contact = await db.get(TrustedContact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    await db.delete(contact)
    await db.flush()
    return None
