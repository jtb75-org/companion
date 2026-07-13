"""Service layer for FCM device token management."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import maintenance_session
from app.models.device_token import DeviceToken


def _refresh_token(token: DeviceToken, platform, device_name, now) -> None:
    token.device_platform = platform
    token.device_name = device_name
    token.is_active = True
    token.last_used_at = now


async def _release_token_other_user(fcm_token: str, user_id: UUID) -> int:
    """Release ``fcm_token`` from any OTHER user (cross-tenant, scoped bypass).

    fcm_token is globally UNIQUE and a physical device legitimately moves
    between users (e.g. a shared phone changing owners) — but under per-user
    RLS the member session cannot see, let alone reassign, another user's row.
    This helper does exactly ONE cross-tenant thing on the maintenance
    (BYPASSRLS) connection: delete the conflicting row so the caller can insert
    its own (RLS-fenced, WITH CHECK) row. Anything wider stays on the member
    session. Returns the number of rows released.
    """
    async with maintenance_session() as mdb:
        result = await mdb.execute(
            delete(DeviceToken).where(
                DeviceToken.fcm_token == fcm_token,
                DeviceToken.user_id != user_id,
            )
        )
        await mdb.commit()
        return result.rowcount or 0


async def register_token(
    db: AsyncSession,
    user_id: UUID,
    fcm_token: str,
    platform: str,
    device_name: str | None = None,
) -> DeviceToken:
    """Register or update an FCM device token.

    If the token exists for THIS user, refresh it (common case, fully
    RLS-scoped). If it exists for a DIFFERENT user (device changed hands),
    release it via the scoped maintenance helper, then insert our own row.
    """
    now = datetime.utcnow()

    # Same-user case on the member session (visible under RLS).
    result = await db.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == user_id,
            DeviceToken.fcm_token == fcm_token,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        _refresh_token(existing, platform, device_name, now)
        await db.flush()
        return existing

    # Not ours: if another user holds this (globally unique) token, release it
    # cross-tenant, then create our own row. Without the release, the INSERT
    # below would hit the unique constraint (the other row is invisible to a
    # member session under RLS).
    #
    # The release (a committed maintenance txn) and our insert are two steps, so
    # a concurrent register for the same token can re-claim it in between and our
    # flush hits the unique constraint. Retry once inside a SAVEPOINT so the
    # IntegrityError doesn't poison the request transaction; on the retry the
    # token may now belong to THIS user (a concurrent register for us committed),
    # in which case refresh it instead of inserting.
    last_exc: IntegrityError | None = None
    for _ in range(2):
        await _release_token_other_user(fcm_token, user_id)
        try:
            async with db.begin_nested():
                token = DeviceToken(
                    user_id=user_id,
                    fcm_token=fcm_token,
                    device_platform=platform,
                    device_name=device_name,
                    is_active=True,
                    last_used_at=now,
                )
                db.add(token)
                await db.flush()
            return token
        except IntegrityError as exc:
            last_exc = exc
            result = await db.execute(
                select(DeviceToken).where(
                    DeviceToken.user_id == user_id,
                    DeviceToken.fcm_token == fcm_token,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                _refresh_token(existing, platform, device_name, now)
                await db.flush()
                return existing
    raise last_exc


async def deactivate_token(
    db: AsyncSession,
    user_id: UUID,
    fcm_token: str,
) -> bool:
    """Deactivate a specific FCM token for a user.

    Returns True if a token was found and deactivated.
    """
    result = await db.execute(
        select(DeviceToken).where(
            DeviceToken.user_id == user_id,
            DeviceToken.fcm_token == fcm_token,
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        return False

    token.is_active = False
    await db.flush()
    return True


async def deactivate_all_tokens(
    db: AsyncSession,
    user_id: UUID,
) -> int:
    """Deactivate all FCM tokens for a user.

    Returns the number of tokens deactivated.
    """
    result = await db.execute(
        update(DeviceToken)
        .where(
            DeviceToken.user_id == user_id,
            DeviceToken.is_active.is_(True),
        )
        .values(is_active=False)
    )
    await db.flush()
    return result.rowcount


async def get_active_tokens(
    db: AsyncSession,
    user_id: UUID,
) -> list[str]:
    """Return active FCM token strings for a user."""
    result = await db.execute(
        select(DeviceToken.fcm_token).where(
            DeviceToken.user_id == user_id,
            DeviceToken.is_active.is_(True),
        )
    )
    return list(result.scalars().all())
