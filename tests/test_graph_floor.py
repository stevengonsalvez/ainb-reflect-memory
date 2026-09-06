"""The classification floor on the shared-store (Mode 2) write path.

A restricted or pii note may live in the local markdown KB but must never
reach ng_kv or ng_vectors. The graph engine skips it before nano-graphrag
sees it, and the KV adapter refuses a full document that slips past.
"""

from __future__ import annotations

import asyncio

import pytest

RESTRICTED = "---\ntitle: secret\nclassification: restricted\n---\n\nnever shared\n"
INTERNAL = "---\ntitle: ok\nclassification: internal\n---\n\nshareable\n"
UNLABELLED = "---\ntitle: legacy\n---\n\nlegacy note\n"


class _FakeGraph:
    def __init__(self) -> None:
        self.inserted: list = []

    def insert(self, text):
        self.inserted.append(text)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    from reflect_kb.cli import graph_engine as ge

    eng = ge.LearningsGraphEngine.__new__(ge.LearningsGraphEngine)
    eng._cache_dir = tmp_path
    eng._graph = _FakeGraph()
    eng._model = None
    eng._pending_entities = None
    from collections import deque

    eng._entity_queue = deque()
    eng._pg_dsn = None
    eng._workspace_id = None
    monkeypatch.setattr(eng, "_init_graph", lambda: None)
    return eng


def test_mode1_indexes_everything(engine) -> None:
    engine.insert_documents_batch([(RESTRICTED, None), (INTERNAL, None), (UNLABELLED, None)])
    assert engine._graph.inserted == [[RESTRICTED, INTERNAL, UNLABELLED]]


def test_mode2_skips_restricted_and_pii_notes(engine) -> None:
    engine._pg_dsn = "postgresql://u@localhost/x"
    engine._workspace_id = "11111111-1111-1111-1111-111111111111"
    engine.insert_documents_batch([(RESTRICTED, None), (INTERNAL, None), (UNLABELLED, None)])
    assert engine._graph.inserted == [[INTERNAL, UNLABELLED]]
    engine.insert_document(RESTRICTED.replace("restricted", "pii"))
    engine.insert_document(INTERNAL)
    assert engine._graph.inserted[1:] == [INTERNAL]


def test_kv_adapter_refuses_a_restricted_full_doc() -> None:
    pytest.importorskip("nano_graphrag")
    from reflect_kb.postgres.nanographrag.kv import PgKVStorage

    class _Pg:
        def __init__(self) -> None:
            self.rows: list = []

        def executemany(self, _sql, rows):
            self.rows.extend(rows)

    kv = PgKVStorage.__new__(PgKVStorage)
    kv.namespace = "full_docs"
    kv._pg = _Pg()
    kv._ws = "11111111-1111-1111-1111-111111111111"
    asyncio.run(kv.upsert({"doc-r": {"content": RESTRICTED}, "doc-i": {"content": INTERNAL}}))
    assert [r[2] for r in kv._pg.rows] == ["doc-i"]
