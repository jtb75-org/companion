"""Bootstrap endpoint — creates the first admin user.

Only works when the admin_users table is empty (no existing admins).
After the first admin is created, this endpoint returns 403.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.admin_user import AdminUser

router = APIRouter(tags=["Bootstrap"])

_bootstrap_attempts = 0
_MAX_BOOTSTRAP_ATTEMPTS = 3


@router.post("/api/v1/auth/bootstrap-admin")
async def bootstrap_first_admin(
    db: AsyncSession = Depends(get_db),
    email: str = "dd@mydailydignity.com",
):
    """Create the first admin user. Only works when no admins exist."""
    global _bootstrap_attempts
    _bootstrap_attempts += 1
    if _bootstrap_attempts > _MAX_BOOTSTRAP_ATTEMPTS:
        raise HTTPException(429, "Too many bootstrap attempts")

    # Check if any admins exist
    result = await db.execute(
        select(func.count()).select_from(AdminUser)
    )
    count = result.scalar()

    if count > 0:
        raise HTTPException(
            status_code=403,
            detail="Admin users already exist. Use the admin panel to add more.",
        )

    admin = AdminUser(
        email=email,
        name="Admin",
        role="admin",
        is_active=True,
    )
    db.add(admin)
    await db.flush()

    return {
        "created": True,
        "email": email,
        "role": "admin",
        "id": str(admin.id),
    }
