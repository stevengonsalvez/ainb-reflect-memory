# ABOUTME: Fixtures for the Postgres backend tests (MemoryStore + nano-graphrag
# ABOUTME: adapters). Auto-skips the integration tier when no Postgres is
# ABOUTME: reachable; the no-DB tests need none of this.

from __future__ import annotations

import hashlib
import pathlib

import pytest

from _support.pg import WS_A, WS_B, connect_or_skip, disposable_database  # noqa: F401

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_M1 = _MIGRATIONS / "0001_reflect_memory_phase1.sql"
_M2 = _MIGRATIONS / "0002_nanographrag_pgvector.sql"


@pytest.fixture(scope="session")
def _migrated_dsn():
    """Create a reflect_test_<random> database on the localhost server named
    by REFLECT_TEST_DATABASE_URL or DATABASE_URL, apply both migrations once
    per session, drop it at the end; skip cleanly when no local server is
    configured. The developer's own databases are never touched."""
    with disposable_database() as dsn:
        conn = connect_or_skip(dsn)
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(_M1.read_text())
                    cur.execute(_M2.read_text())
                except Exception as exc:  # noqa: BLE001, e.g. pgvector missing
                    pytest.skip(f"migrations did not apply ({exc})")
        finally:
            conn.close()
        yield dsn



# Alias used by the nano-graphrag tests.
@pytest.fixture
def pg_dsn(_migrated_dsn):
    return _migrated_dsn


@pytest.fixture
def conn(_migrated_dsn):
    """Fresh, truncated mapping-row connection per test (Phase-1 MemoryStore)."""
    import psycopg
    from psycopg.rows import dict_row

    c = psycopg.connect(_migrated_dsn, row_factory=dict_row)
    with c.cursor() as cur:
        cur.execute(
            "truncate reflect_memory.memory_items, reflect_memory.entities, "
            "reflect_memory.edges cascade;"
        )
    c.commit()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def store(conn):
    from reflect_kb.postgres import MemoryStore

    return MemoryStore(conn)


@pytest.fixture
def clean(_migrated_dsn):
    """Truncate ALL reflect_memory tables before each nano-graphrag test."""
    import psycopg

    c = psycopg.connect(_migrated_dsn, autocommit=True)
    with c.cursor() as cur:
        cur.execute(
            "truncate reflect_memory.ng_kv, reflect_memory.ng_graph_nodes, "
            "reflect_memory.ng_graph_edges, reflect_memory.ng_vectors, "
            "reflect_memory.memory_items, reflect_memory.entities, "
            "reflect_memory.edges cascade;"
        )
    c.close()
    return _migrated_dsn


@pytest.fixture
def fake_embedding():
    """Deterministic 768-d unit-vector embedding func (no model, no network)."""
    import numpy as np

    def _vec(text: str):
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(768)
        return v / np.linalg.norm(v)

    async def embedding_func(texts):
        return np.array([_vec(t) for t in texts])

    embedding_func.embedding_dim = 768
    return embedding_func
