"""Admin API — Admin user management."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db import get_db
from app.integrations.authentik_admin import provision_authentik_account
from app.models.admin_user import AdminUser as AdminUserModel
from app.schemas.admin import AdminUserCreate

router = APIRouter(prefix="/admin/admin-users", tags=["Admin - Users"])

_admin = require_admin_role("admin")


@router.get("")
async def list_admin_users(
    admin: AdminUser = Depends(_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all admin users."""
    result = await db.execute(
        select(AdminUserModel).order_by(AdminUserModel.email)
    )
    users = list(result.scalars().all())
    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "is_active": u.is_active,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ],
        "total": len(users),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_admin_user(
    data: AdminUserCreate,
    admin: AdminUser = Depends(_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new admin user."""
    new_user = AdminUserModel(
        email=data.email,
        name=data.name,
        role=data.role,
    )
    db.add(new_user)
    await db.flush()
    # Provision a matching Authentik account (branded BFF provisioning, PR 1).
    # Commit first so a provisioning error can never roll back the row; the call
    # is best-effort + idempotent + inert on the Firebase default.
    await db.commit()
    await provision_authentik_account(new_user.email, new_user.name)
    return new_user


@router.patch("/{user_id}")
async def update_admin_user(
    user_id: uuid.UUID,
    data: AdminUserCreate,
    admin: AdminUser = Depends(_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an admin user."""
    result = await db.execute(
        select(AdminUserModel).where(AdminUserModel.id == user_id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Admin user not found")
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(target, key, value)
    await db.flush()
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin_user(
    user_id: uuid.UUID,
    admin: AdminUser = Depends(_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an admin user."""
    result = await db.execute(
        select(AdminUserModel).where(AdminUserModel.id == user_id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Admin user not found")
    target.is_active = False
    await db.flush()
    return None
