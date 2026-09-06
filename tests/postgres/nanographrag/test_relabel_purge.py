"""A note indexed under a shareable label and relabelled restricted: the floor
stops the new write, and the purge removes every ng_* row and graph node or
edge the old label left behind."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("nano_graphrag")

sys.path.insert(0, str(Path(__file__).parent))
from test_cross_machine_graphrag import WS
from test_floor_before_chunking import INTERNAL, _engine

pytestmark = pytest.mark.integration

RESTRICTED_AUTH = INTERNAL.replace("classification: internal", "classification: restricted")
EDITED_RESTRICTED = RESTRICTED_AUTH.replace("validates the JWT token", "validates and refreshes the JWT token")


def _rows(dsn) -> dict[str, int]:
    import psycopg

    c = psycopg.connect(dsn, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute("select namespace, count(*) from reflect_memory.ng_kv where workspace_id=%s group by 1", (WS,))
            out = {f"kv:{ns}": n for ns, n in cur.fetchall()}
            cur.execute("select count(*) from reflect_memory.ng_kv where workspace_id=%s and value::text ilike %s",
                        (WS, "%auth middleware%"))
            out["kv_with_text"] = cur.fetchone()[0]
            cur.execute("select namespace, count(*) from reflect_memory.ng_vectors where workspace_id=%s group by 1", (WS,))
            out.update({f"vec:{ns}": n for ns, n in cur.fetchall()})
            cur.execute("select count(*) from reflect_memory.ng_graph_nodes where workspace_id=%s", (WS,))
            out["nodes"] = cur.fetchone()[0]
            cur.execute("select count(*) from reflect_memory.ng_graph_edges where workspace_id=%s", (WS,))
            out["edges"] = cur.fetchone()[0]
        return out
    finally:
        c.close()


def test_relabel_after_index_purges_every_ng_row(clean, tmp_path) -> None:
    dsn = clean
    eng = _engine(tmp_path / "a", dsn)
    assert eng.insert_documents_batch([(INTERNAL, None, "internal")]) == 1
    before = _rows(dsn)
    assert before.get("kv:full_docs") == 1 and before["kv_with_text"] >= 1 and before["nodes"] >= 1, before

    eng2 = _engine(tmp_path / "b", dsn)
    assert eng2.insert_documents_batch([(RESTRICTED_AUTH, None, "restricted")]) == 0
    assert eng2.purge_local_only([RESTRICTED_AUTH]) == 1
    after = _rows(dsn)
    assert after.get("kv:full_docs", 0) == 0 and after.get("kv:text_chunks", 0) == 0, after
    assert after["kv_with_text"] == 0 and after.get("vec:chunks", 0) == 0, after
    assert after["nodes"] == 0 and after["edges"] == 0 and after.get("vec:entities", 0) == 0, after
    assert after.get("kv:community_reports", 0) == 0
    # Nothing left to purge, and a purge of an unknown note is a no-op.
    assert eng2.purge_local_only([RESTRICTED_AUTH]) == 0


def test_relabel_with_edits_still_purges_by_title(clean, tmp_path) -> None:
    dsn = clean
    eng = _engine(tmp_path / "a", dsn)
    assert eng.insert_documents_batch([(INTERNAL, None, "internal")]) == 1
    eng2 = _engine(tmp_path / "b", dsn)
    assert eng2.purge_local_only([EDITED_RESTRICTED]) == 1
    after = _rows(dsn)
    assert after.get("kv:full_docs", 0) == 0 and after["kv_with_text"] == 0, after


def test_mode1_engine_purges_nothing(tmp_path) -> None:
    from reflect_kb.cli import graph_engine as ge

    eng = ge.LearningsGraphEngine.__new__(ge.LearningsGraphEngine)
    eng._pg_dsn = None
    eng._workspace_id = None
    assert eng.purge_local_only([RESTRICTED_AUTH]) == 0
