"""Mode 2 floor, applied once before chunking: a restricted note handed to the
graph engine leaves zero rows in every ng_* namespace, and a second run
converges (full_docs and text_chunks do not grow)."""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import pytest

pytest.importorskip("nano_graphrag")

sys.path.insert(0, str(Path(__file__).parent))
from test_cross_machine_graphrag import WS, _make_graph

pytestmark = pytest.mark.integration

RESTRICTED = "---\ntitle: vault\nclassification: restricted\n---\n\nThe vault secret rotates nightly.\n"
INTERNAL = "---\ntitle: auth\nclassification: internal\n---\n\nThe auth middleware validates the JWT token.\n"


def _engine(tmp_path, dsn):
    from reflect_kb.cli import graph_engine as ge

    eng = ge.LearningsGraphEngine.__new__(ge.LearningsGraphEngine)
    eng._cache_dir = tmp_path
    eng._graph = _make_graph(tmp_path / "graph", dsn)
    eng._model = None
    eng._pending_entities = None
    eng._entity_queue = deque()
    eng._pg_dsn = dsn
    eng._workspace_id = WS
    return eng


def _counts(dsn):
    import psycopg

    c = psycopg.connect(dsn, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute("select namespace, count(*) from reflect_memory.ng_kv where workspace_id=%s group by 1", (WS,))
            kv = dict(cur.fetchall())
            cur.execute("select count(*) from reflect_memory.ng_kv where workspace_id=%s and value::text ilike %s",
                        (WS, "%vault secret%"))
            leaked_kv = cur.fetchone()[0]
            cur.execute("select count(*) from reflect_memory.ng_vectors where workspace_id=%s and meta::text ilike %s",
                        (WS, "%vault%"))
            leaked_vec = cur.fetchone()[0]
        return kv, leaked_kv + leaked_vec
    finally:
        c.close()


def test_restricted_note_never_reaches_any_ng_namespace_and_reindex_converges(clean, tmp_path) -> None:
    dsn = clean
    eng = _engine(tmp_path / "a", dsn)
    assert eng.insert_documents_batch([(RESTRICTED, None, "restricted"), (INTERNAL, None, "internal")]) == 1
    kv1, leaked = _counts(dsn)
    assert leaked == 0, "restricted content reached the shared store"
    assert kv1.get("full_docs") == 1 and kv1.get("text_chunks", 0) >= 1, kv1
    eng2 = _engine(tmp_path / "b", dsn)
    assert eng2.insert_documents_batch([(RESTRICTED, None, "restricted"), (INTERNAL, None, "internal")]) == 1
    kv2, leaked2 = _counts(dsn)
    assert leaked2 == 0
    assert kv2.get("full_docs") == kv1.get("full_docs") and kv2.get("text_chunks") == kv1.get("text_chunks"), (kv1, kv2)
