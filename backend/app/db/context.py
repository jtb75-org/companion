"""Transaction-local Postgres GUCs for per-user RLS (WS1 Phase 2).

`app.current_user_id` scopes every RLS-protected tenant table to the member
whose data a request (or worker iteration) is operating on. `app.current_login_
email` lets the auth bootstrap lookup on `users` pass its RLS policy — auth
resolves a member by email *before* the user_id/GUC is known, so the `users`
policy allows a row whose email matches this GUC (swapped for the OIDC subject
when Authentik lands).

Both use `set_config(key, value, is_local => true)` — the parameterized,
transaction-local form of `SET LOCAL`. Transaction-local is the whole point:
the value is released at COMMIT/ROLLBACK and never leaks across pooled
connections (a plain session `SET` bleeds into the next checkout — a cross-user
data hazard that is hell to reproduce). Callers must be inside a transaction
(the request/`get_db` txn, or a worker's per-iteration txn); SQLAlchemy autobegin
satisfies this on first execute.

Until the RLS policies are enabled (Phase 2d+), setting these GUCs is a no-op —
nothing reads them yet — so this can ship ahead of the policies safely.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_USER_ID = text(
    "SELECT set_config('app.current_user_id', :v, true)"
)
_SET_LOGIN_EMAIL = text(
    "SELECT set_config('app.current_login_email', :v, true)"
)
_SET_LOGIN_SUBJECT = text(
    "SELECT set_config('app.current_login_subject', :v, true)"
)


async def set_user_context(db: AsyncSession, user_id) -> None:
    """Set the tenant context to ``user_id`` (transaction-local)."""
    await db.execute(_SET_USER_ID, {"v": str(user_id)})


async def clear_user_context(db: AsyncSession) -> None:
    """Reset the tenant context (empty → fail-closed). For worker loops that
    reuse a transaction across users, call before switching users."""
    await db.execute(_SET_USER_ID, {"v": ""})


async def set_login_email_context(db: AsyncSession, email: str) -> None:
    """Set the bootstrap email so the pre-context `users` lookup passes RLS."""
    await db.execute(_SET_LOGIN_EMAIL, {"v": email or ""})


async def set_login_subject_context(db: AsyncSession, subject: str) -> None:
    """Set the bootstrap OIDC subject so the pre-context `users` lookup-by-subject
    (Authentik login) passes RLS. Read-only bootstrap: the users policy WITH CHECK
    still fences writes to the tenant id GUC."""
    await db.execute(_SET_LOGIN_SUBJECT, {"v": subject or ""})
