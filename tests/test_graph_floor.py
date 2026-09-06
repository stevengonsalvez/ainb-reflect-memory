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


def test_mode2_uses_the_parsed_label_without_reparsing(engine, monkeypatch) -> None:
    """The CLI hands the already-parsed label down; the engine parses the text
    only when no label is given, and reports how many docs it indexed."""
    engine._pg_dsn = "postgresql://x"
    engine._workspace_id = "ws"
    from reflect_kb import classification as cls

    calls = []
    monkeypatch.setattr(cls, "classification_of_note", lambda text: calls.append(text) or "internal")
    n = engine.insert_documents_batch([(UNLABELLED, None, "restricted"), (INTERNAL, None, "internal"), (UNLABELLED, None)])
    assert n == 2 and len(engine._graph.inserted[0]) == 2
    assert calls == [UNLABELLED], "the text was parsed only for the doc without a label"


def test_kv_adapter_writes_what_the_engine_hands_it() -> None:
    """The ng_* floor is engine-level, before chunking: the KV adapter no
    longer drops full_docs on its own (that only re-chunked the doc as new on
    every run while text_chunks kept it)."""
    from reflect_kb.postgres.nanographrag.kv import PgKVStorage

    class _Pg:
        def __init__(self) -> None:
            self.rows = []

        def executemany(self, sql, rows):
            self.rows.extend(rows)

    store = PgKVStorage.__new__(PgKVStorage)
    store._pg, store._ws, store.namespace = _Pg(), "ws", "full_docs"
    asyncio.run(store.upsert({"doc-1": {"content": RESTRICTED}}))
    assert [r[2] for r in store._pg.rows] == ["doc-1"]
