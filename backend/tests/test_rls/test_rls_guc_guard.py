"""Unit tests for the RLS unset-GUC guard decision logic (WS1 Phase 2f-ii).

Tests `_handle` directly (no DB): a fake connection carrying the shadow-state
`info` dict, feeding it set_config statements and data statements, asserting when
a warning is emitted. Covers the users email-bootstrap asymmetry.
"""

from __future__ import annotations

import logging

import pytest

from app.db import rls_guard


class _FakeConn:
    def __init__(self):
        self.info = {}


def _set_uid(conn, value):
    rls_guard._handle(
        conn, "SELECT set_config('app.current_user_id', :v, true)", {"v": value}, None
    )


def _set_email(conn, value):
    rls_guard._handle(
        conn,
        "SELECT set_config('app.current_login_email', :v, true)",
        {"v": value},
        None,
    )


@pytest.fixture(autouse=True)
def _clear_dedupe():
    rls_guard._seen_warnings.clear()
    yield
    rls_guard._seen_warnings.clear()


def _warned(caplog, conn, sql, exec_opts=None):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.db.rls_guard"):
        rls_guard._handle(conn, sql, None, exec_opts)
    return any("RLS unset-GUC" in r.message for r in caplog.records)


def test_setter_updates_shadow_state():
    conn = _FakeConn()
    _set_uid(conn, "11111111-1111-1111-1111-111111111111")
    assert conn.info[rls_guard._INFO_UID] is True
    _set_uid(conn, "")  # clear_user_context
    assert conn.info[rls_guard._INFO_UID] is False


def test_tenant_table_without_uid_warns(caplog):
    conn = _FakeConn()
    assert _warned(caplog, conn, "SELECT id FROM todos WHERE user_id = $1")


def test_tenant_table_with_uid_ok(caplog):
    conn = _FakeConn()
    _set_uid(conn, "abc")
    assert not _warned(caplog, conn, "SELECT id FROM todos WHERE user_id = $1")


def test_users_with_email_bootstrap_ok(caplog):
    conn = _FakeConn()
    _set_email(conn, "member@example.com")
    assert not _warned(caplog, conn, "SELECT id FROM users WHERE email = $1")


def test_users_with_no_guc_warns(caplog):
    conn = _FakeConn()
    assert _warned(caplog, conn, "SELECT id FROM users WHERE email = $1")


def test_email_is_not_a_global_substitute(caplog):
    """Only-login-email set, then a non-users tenant table is queried → warn."""
    conn = _FakeConn()
    _set_email(conn, "member@example.com")
    assert _warned(
        caplog, conn, "SELECT id FROM trusted_contacts WHERE user_id = $1"
    )


def test_join_with_tenant_table_needs_uid(caplog):
    conn = _FakeConn()
    _set_email(conn, "member@example.com")  # email only
    # users JOIN documents — the non-users table forces the uid requirement.
    assert _warned(
        caplog,
        conn,
        "SELECT u.id FROM users u JOIN documents d ON d.user_id = u.id",
    )


def test_skip_execution_option_suppresses(caplog):
    conn = _FakeConn()
    assert not _warned(
        caplog,
        conn,
        "SELECT id FROM todos",
        exec_opts={"skip_rls_guc_guard": True},
    )


def test_non_tenant_table_ignored(caplog):
    conn = _FakeConn()
    assert not _warned(caplog, conn, "SELECT id FROM admin_users WHERE email = $1")


def test_substring_name_not_matched(caplog):
    """A column/table whose name merely contains a tenant name must not trip it."""
    conn = _FakeConn()
    assert not _warned(caplog, conn, "SELECT id FROM todos_archive WHERE x = 1")


def test_document_chunks_not_shadowed_by_documents(caplog):
    conn = _FakeConn()
    assert _warned(caplog, conn, "SELECT id FROM document_chunks WHERE user_id = $1")


def test_dedupe_warns_once_per_callsite(caplog):
    conn = _FakeConn()
    assert _warned(caplog, conn, "SELECT id FROM bills WHERE user_id = $1")
    # Same callsite+tables → suppressed on the second identical statement.
    assert not _warned(caplog, conn, "SELECT id FROM bills WHERE user_id = $1")
