"""The graph adapter binds the tenant with SET LOCAL inside every statement's
transaction, so a transaction-mode pooler that hands each statement to a
different backend (no session GUC) still sees the workspace, and nothing is
left bound at session level between statements."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

WS = "0bbbbbbb-0000-4000-8000-00000000bbbb"


def test_every_statement_binds_locally_and_survives_a_session_reset(clean) -> None:
    from reflect_kb.postgres.nanographrag._conn import PgBackend

    be = PgBackend(clean, WS)
    be.execute("insert into reflect_memory.ng_kv (workspace_id, namespace, key, value) values (%s, 'full_docs', 'k1', '{}'::jsonb)",
               (WS,))
    # Simulate the pooler: a fresh backend has no session-level GUC. The
    # adapter never relies on one, so a reset between statements changes nothing.
    be._conn.execute("reset app.current_workspace")
    rows = be.fetchall("select key from reflect_memory.ng_kv where workspace_id = %s and namespace = 'full_docs'", (WS,))
    assert [r["key"] for r in rows] == ["k1"]
    assert be.fetchone("select current_setting('app.current_workspace', true) as ws")["ws"] == WS
    # Outside the adapter's transactions the session carries no binding.
    assert (be._conn.execute("select current_setting('app.current_workspace', true)").fetchone()["current_setting"] or "") == ""
    be.run_tx([
        ("insert into reflect_memory.ng_kv (workspace_id, namespace, key, value) values (%s, 'full_docs', 'k2', '{}'::jsonb)", (WS,), False),
        ("select current_setting('app.current_workspace', true)", None, False),
    ])
    rows = be.fetchall("select key from reflect_memory.ng_kv where workspace_id = %s order by key", (WS,))
    assert [r["key"] for r in rows] == ["k1", "k2"]
