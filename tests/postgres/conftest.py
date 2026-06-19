# ABOUTME: Fixtures for the Postgres backend tests (MemoryStore + nano-graphrag
# ABOUTME: adapters). Auto-skips the integration tier when no Postgres is
# ABOUTME: reachable; the no-DB tests need none of this.

from __future__ import annotations

import hashlib
import os
import pathlib

import pytest

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_M1 = _MIGRATIONS / "0001_reflect_memory_phase1.sql"
_M2 = _MIGRATIONS / "0002_nanographrag_pgvector.sql"

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"


def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("REFLECT_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def _migrated_dsn() -> str:
    """Apply both migrations once per session; skip cleanly if no DB/psycopg."""
    dsn = _dsn()
    if not dsn:
        pytest.skip("no DATABASE_URL — Postgres integration tests skipped")
    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
    try:
        conn = psycopg.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable ({exc})")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            try:
                cur.execute(_M1.read_text())
                cur.execute(_M2.read_text())
            except Exception as exc:  # noqa: BLE001 — e.g. pgvector missing
                pytest.skip(f"migrations did not apply ({exc})")
    finally:
        conn.close()
    return dsn


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
