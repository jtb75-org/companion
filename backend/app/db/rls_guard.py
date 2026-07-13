"""Loud unset-GUC guard for per-user RLS (WS1 Phase 2f-ii).

Observability, NOT a security control — RLS itself is the control and already
fails closed. This catches the one bug RLS makes SILENT: a query that hits an
RLS tenant table on the app (companion_app) connection without
``app.current_user_id`` set returns 0 rows with no error, i.e. a latent "member
sees nothing" bug from a forgotten ``set_user_context``.

Design (per niru): a ``before_cursor_execute`` listener on the APP engine only,
warn-only, never raising. It shadows the two transaction-local GUCs per DBAPI
connection (learned from the actual ``set_config`` statements, reset on
commit/rollback/checkin) and warns when a data statement references a tenant
table whose required GUC is absent:

- ``users``: OK if app.current_user_id OR app.current_login_email is set
  (the auth-by-email bootstrap legitimately queries users pre-user-id).
- every OTHER RLS tenant table: OK only if app.current_user_id is set. A request
  that set only the login-email GUC and then touched trusted_contacts/documents/…
  before set_user_context SHOULD warn — login-email is not a global substitute.

Escape hatch: ``.execution_options(skip_rls_guc_guard=True)`` on a statement or
connection suppresses the guard for a known-safe global/maintenance query.
"""

from __future__ import annotations

import logging
import re
import traceback

logger = logging.getLogger("app.db.rls_guard")

# The RLS tenant tables (migrations 023–031). `users` is special (email bootstrap).
_USERS = "users"
_OTHER_TENANT_TABLES = frozenset(
    {
        "todos",
        "functional_memory",
        "documents",
        "document_chunks",
        "bills",
        "appointments",
        "medications",
        "pending_reviews",
        "questions_tracker",
        "chat_sessions",
        "device_tokens",
        "chat_messages",
        "medication_confirmations",
        "caregiver_activity_log",
        "user_encryption_keys",
        "trusted_contacts",
        "caregiver_assignment_requests",
    }
)
_ALL_TENANT_TABLES = _OTHER_TENANT_TABLES | {_USERS}

# Match a tenant table name as a whole identifier, optionally quoted and optionally
# schema-qualified (public.), not a substring of a larger identifier. Longest names
# first so e.g. `documents` doesn't shadow `document_chunks`.
_names = "|".join(sorted(_ALL_TENANT_TABLES, key=len, reverse=True))
_TABLE_RE = re.compile(
    r'(?<![\w"])(?:public\.)?"?(' + _names + r')"?(?![\w"])', re.IGNORECASE
)
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

_INFO_UID = "rls_guc_uid_set"
_INFO_EMAIL = "rls_guc_email_set"

# Bounded de-dupe so a hot forgotten path doesn't spam logs.
_seen_warnings: set[tuple] = set()
_SEEN_CAP = 512


def _extract_setter_value(parameters) -> str | None:
    """Pull the bind value out of a `SELECT set_config(<name>, :v, true)` call."""
    try:
        if isinstance(parameters, dict):
            return parameters.get("v")
        if isinstance(parameters, (list, tuple)) and parameters:
            first = parameters[0]
            # executemany passes a sequence of param sets; setters never do.
            if isinstance(first, (list, tuple, dict)):
                return None
            return first
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _caller() -> str:
    """First app frame outside the db plumbing — points at the missed callsite."""
    for frame in reversed(traceback.extract_stack()[:-2]):
        fn = frame.filename
        if "/app/" in fn and "/app/db/" not in fn:
            return f"{fn.rsplit('/app/', 1)[1]}:{frame.lineno} in {frame.name}"
    return "<unknown>"


def _referenced_tables(sql: str) -> set[str]:
    stripped = _COMMENT_RE.sub(" ", sql)
    return {m.group(1).lower() for m in _TABLE_RE.finditer(stripped)}


def _handle(conn, statement: str, parameters, execution_options) -> None:
    if not statement:
        return
    lowered = statement.lower()

    # GUC setters/readers: update shadow state, never warn on them.
    if "set_config(" in lowered or "current_setting(" in lowered:
        if "app.current_user_id" in lowered:
            val = _extract_setter_value(parameters)
            conn.info[_INFO_UID] = bool(val and str(val).strip())
        if "app.current_login_email" in lowered:
            val = _extract_setter_value(parameters)
            conn.info[_INFO_EMAIL] = bool(val and str(val).strip())
        return

    if execution_options and execution_options.get("skip_rls_guc_guard"):
        return

    tables = _referenced_tables(statement)
    if not tables:
        return

    uid_set = bool(conn.info.get(_INFO_UID))
    email_set = bool(conn.info.get(_INFO_EMAIL))
    non_users = tables - {_USERS}

    if non_users:
        # Any non-users tenant table needs the tenant GUC, full stop.
        if uid_set:
            return
        missing = "app.current_user_id"
        offending = non_users
    else:
        # users-only: the email bootstrap is acceptable.
        if uid_set or email_set:
            return
        missing = "app.current_user_id or app.current_login_email"
        offending = tables

    caller = _caller()
    key = (caller, tuple(sorted(offending)))
    if key in _seen_warnings:
        return
    if len(_seen_warnings) < _SEEN_CAP:
        _seen_warnings.add(key)
    logger.warning(
        "RLS unset-GUC: query touched %s with no %s set — RLS fails closed to 0 "
        "rows here. Missing set_user_context? callsite=%s",
        ", ".join(sorted(offending)),
        missing,
        caller,
    )


def _guard_enabled(settings) -> bool:
    mode = (settings.rls_guc_guard or "auto").lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return settings.environment != "prod"  # auto


def install_rls_guc_guard(async_engine, settings) -> bool:
    """Attach the guard to ``async_engine`` (the APP engine only — never the
    maintenance engine). No-op + returns False when disabled by config."""
    if not _guard_enabled(settings):
        return False

    from sqlalchemy import event

    sync_engine = async_engine.sync_engine

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        try:
            exec_opts = getattr(context, "execution_options", None) if context else None
            _handle(conn, statement, parameters, exec_opts)
        except Exception:  # never let diagnostics break a query
            logger.debug("rls_guc_guard listener error", exc_info=True)

    def _reset(conn, *_a):
        conn.info.pop(_INFO_UID, None)
        conn.info.pop(_INFO_EMAIL, None)

    def _reset_record(dbapi_conn, rec):
        rec.info.pop(_INFO_UID, None)
        rec.info.pop(_INFO_EMAIL, None)

    # Transaction-local GUCs clear at commit/rollback; pool checkin is belt-and-
    # suspenders for a returned connection. Connection.info is the persistent
    # per-DBAPI-connection dict (same object the record exposes as .info), so
    # popping our two keys is enough — don't clobber other users of it.
    event.listen(sync_engine, "commit", _reset)
    event.listen(sync_engine, "rollback", _reset)
    event.listen(sync_engine, "checkin", _reset_record)
    return True
