from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Maintenance (cross-user) session — WS1 Phase 2c ────────────────────────────
# A SEPARATE connection as the BYPASSRLS `companion_maintenance` role, for the
# internal/worker cross-user discovery scans that per-user RLS would fail-close.
# Lazily built (no connection until first use) so it's inert until a worker needs
# it and the credential exists. The scoped-bypass discipline (kali): use the
# bypass ONLY for the cross-user read, then `SET LOCAL ROLE companion_app` +
# set app.current_user_id for the per-user writes so mutations stay RLS-fenced.
# `companion_app` is deliberately NOT a member of `companion_maintenance`, so the
# normal runtime can never escalate to bypass.
_maintenance_engine = None
_maintenance_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_maintenance_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the companion_maintenance (BYPASSRLS) role."""
    global _maintenance_engine, _maintenance_session_factory
    if _maintenance_session_factory is None:
        url = settings.maintenance_database_url
        if not url:
            raise RuntimeError(
                "COMPANION_MAINTENANCE_DATABASE_URL is not configured — required "
                "for cross-user worker discovery under RLS (WS1 Phase 2c)."
            )
        _maintenance_engine = create_async_engine(
            url, pool_size=5, max_overflow=5, pool_pre_ping=True
        )
        _maintenance_session_factory = async_sessionmaker(
            _maintenance_engine, class_=AsyncSession, expire_on_commit=False
        )
    return _maintenance_session_factory


@asynccontextmanager
async def maintenance_session() -> AsyncIterator[AsyncSession]:
    """A session for a worker's cross-user DISCOVERY read (WS1 Phase 2c).

    When `maintenance_database_url` is configured (prod), this is the BYPASSRLS
    `companion_maintenance` connection so the discovery scan isn't fail-closed by
    per-user RLS. When it is NOT configured (dev/test, or prod before the role is
    wired), it falls back to the normal session — safe there because no RLS
    policies exist, so the discovery works either way. Keep the body to the
    discovery query ONLY (kali): do per-user mutations in a `companion_app`
    session with the tenant GUC set, never here under bypass.
    """
    factory = (
        get_maintenance_session_factory()
        if settings.maintenance_database_url
        else async_session_factory
    )
    async with factory() as session:
        yield session
