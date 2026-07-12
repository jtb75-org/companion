"""Tests for RAG vector retrieval.

Guards the HNSW iterative-scan fix: pgvector applies the ``WHERE user_id`` (and,
after the PHI-hardening RLS work, the RLS) filter AFTER the global HNSW ANN scan,
so without ``hnsw.iterative_scan`` a member whose chunks are far from the query's
nearest cluster silently gets zero results. The retrieval path MUST set the GUC
(transaction-locally) before the vector SELECT. See the PHI-hardening Phase 0
spike. Full recall behaviour is covered by the pgvector spike harness against a
live paradedb (SQLite/mock cannot exercise HNSW).
"""

from __future__ import annotations

from uuid import uuid4

from app.conversation import retrieval


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    """Records executed SQL text so we can assert ordering + GUCs."""

    def __init__(self, select_rows=None):
        self.statements: list[str] = []
        self._select_rows = select_rows or []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        # Only the vector SELECT returns rows; SET LOCAL returns nothing useful.
        if "document_chunks" in sql:
            return _FakeResult(self._select_rows)
        return _FakeResult([])


async def test_retrieval_enables_hnsw_iterative_scan_before_select(monkeypatch):
    async def _fake_embed(_query):
        return [0.0] * 768

    monkeypatch.setattr(retrieval, "_embed_query", _fake_embed)

    db = _FakeDB()
    chunks = await retrieval.retrieve_relevant_chunks(
        db, uuid4(), "how much is my electric bill", top_k=5
    )
    assert chunks == []  # no rows -> empty, and no decryption attempted

    # The iterative-scan GUC must be set...
    guc_idxs = [
        i for i, s in enumerate(db.statements) if "hnsw.iterative_scan" in s
    ]
    assert guc_idxs, f"iterative_scan GUC never set; ran: {db.statements}"

    # ...transaction-locally (SET LOCAL, not a session-wide SET that would bleed
    # across pooled connections)...
    assert "LOCAL" in db.statements[guc_idxs[0]].upper()
    assert "relaxed_order" in db.statements[guc_idxs[0]]

    # ...and BEFORE the vector SELECT (a GUC set after the scan is useless).
    select_idx = next(
        i
        for i, s in enumerate(db.statements)
        if "document_chunks" in s and "ORDER BY" in s
    )
    assert guc_idxs[0] < select_idx, (
        f"iterative_scan set at {guc_idxs[0]} but SELECT at {select_idx}: "
        f"{db.statements}"
    )


async def test_retrieval_bounds_scan_and_keeps_user_prefilter(monkeypatch):
    """Belt-and-suspenders: keep the explicit user_id pre-filter (defence in
    depth) and bound the iterative scan with max_scan_tuples."""

    async def _fake_embed(_query):
        return [0.1] * 768

    monkeypatch.setattr(retrieval, "_embed_query", _fake_embed)

    db = _FakeDB()
    await retrieval.retrieve_relevant_chunks(db, uuid4(), "q", top_k=3)

    all_sql = "\n".join(db.statements)
    assert "hnsw.max_scan_tuples" in all_sql
    assert str(retrieval._HNSW_MAX_SCAN_TUPLES) in all_sql
    # The per-user pre-filter must remain in the vector query (RLS is a backstop,
    # not a replacement).
    select = next(s for s in db.statements if "document_chunks" in s)
    assert "dc.user_id = :user_id" in select
