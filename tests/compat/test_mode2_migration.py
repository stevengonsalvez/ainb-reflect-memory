"""Gate 4: Mode 2 migration proof on a disposable Postgres.

Seed legacy rows with no label plus one restricted row, apply 0001 to 0003,
assert the readable error names the row count, relabel, re-apply, assert FORCE
on all seven tables, then prove an owner-role connection with
app.current_workspace set reads its own rows and no others, and that the
broker path does the same.

The fixture connects only through _support.pg, which refuses any DSN that is
not REFLECT_TEST_DATABASE_URL or a localhost DATABASE_URL, so the schema drop
below can never hit a real database. On a branch without 0003 (main) the
whole module skips.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from _support.pg import WS_A, WS_B, connect_or_skip

from .conftest import REPO

pytestmark = pytest.mark.integration

MIGRATIONS = REPO / "supabase" / "migrations"
M = {n: MIGRATIONS / f for n, f in (
    (1, "0001_reflect_memory_phase1.sql"),
    (2, "0002_nanographrag_pgvector.sql"),
    (3, "0003_classification_force_rls.sql"),
)}
SEVEN = ("memory_items", "entities", "edges", "ng_kv", "ng_graph_nodes", "ng_graph_edges", "ng_vectors")

# On a tree without 0003 this module proves nothing and says so: the
# migrations dir must be exactly 0001 and 0002, and the whole module skips
# with the reason. The stack rebase (#37 adds 0003) turns it live.
if not M[3].exists():
    _present = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert _present == [M[1].name, M[2].name], f"unexpected migrations without 0003: {_present}"
    pytest.skip("0003 lands in #37; the Mode 2 proof is not executed on this tree", allow_module_level=True)


@pytest.fixture
def pg(disposable_pg):
    """(conn, dsn) on a fresh reflect_memory schema with 0001 and 0002 applied,
    inside the session's disposable reflect_test_<random> database."""
    from psycopg.rows import dict_row

    dsn = disposable_pg
    conn = connect_or_skip(dsn, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute("drop schema if exists reflect_memory cascade;")
        cur.execute(M[1].read_text())
        try:
            cur.execute(M[2].read_text())
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"0002 did not apply (pgvector?): {exc}")
    yield conn, dsn
    conn.close()


def _seed(cur) -> None:
    for i in range(3):
        cur.execute(
            "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash) "
            "values (%s, 'note', %s, %s)", (WS_A, f"legacy {i} auth token", f"h{i}"))
    cur.execute(
        "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash, metadata) "
        "values (%s, 'note', 'tenant b auth token', 'hb', '{}'::jsonb)", (WS_B,))
    cur.execute(
        "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash, metadata) "
        "values (%s, 'note', 'restricted auth token row', 'hr', '{\"classification\":\"restricted\"}'::jsonb)",
        (WS_A,))


def test_0003_stops_on_legacy_rows_then_forces_all_seven_tables(pg) -> None:
    conn, _ = pg
    import psycopg

    with conn.cursor() as cur:
        _seed(cur)
        with pytest.raises(psycopg.errors.RaiseException) as exc:
            cur.execute(M[3].read_text())
        assert "1 memory_items row(s)" in str(exc.value)
        cur.execute("update reflect_memory.memory_items set metadata = metadata || "
                    "'{\"classification\":\"internal\"}' where content_hash = 'hr';")
        cur.execute(M[3].read_text())
        cur.execute(
            "select relname, relforcerowsecurity from pg_class "
            "where relnamespace = 'reflect_memory'::regnamespace and relname = any(%s)", (list(SEVEN),))
        forced = {r["relname"]: r["relforcerowsecurity"] for r in cur.fetchall()}
    assert set(forced) == set(SEVEN), forced
    assert all(forced.values()), f"not all tables FORCE RLS: {forced}"


@contextmanager
def _owner_role(cur, role: str):
    from psycopg import sql

    ident = sql.Identifier(role)
    cur.execute(sql.SQL(
        "do $$ begin if exists (select 1 from pg_roles where rolname = {lit}) then "
        "execute format('reassign owned by %I to current_user', {lit}); "
        "execute format('drop owned by %I', {lit}); execute format('drop role %I', {lit}); "
        "end if; end $$;").format(lit=sql.Literal(role)))
    cur.execute(sql.SQL("create role {} nologin;").format(ident))
    cur.execute(sql.SQL("grant usage on schema reflect_memory to {};").format(ident))
    cur.execute(sql.SQL("grant execute on all functions in schema reflect_memory to {};").format(ident))
    cur.execute("select tableowner from pg_tables where schemaname='reflect_memory' and tablename='memory_items'")
    original = cur.fetchone()["tableowner"]
    for t in SEVEN:
        cur.execute(sql.SQL("alter table reflect_memory.{} owner to {}").format(sql.Identifier(t), ident))
    try:
        yield
    finally:
        cur.execute("reset role;")
        for t in SEVEN:
            cur.execute(sql.SQL("alter table reflect_memory.{} owner to {}").format(
                sql.Identifier(t), sql.Identifier(original)))
        cur.execute(sql.SQL("drop owned by {r}; drop role {r};").format(r=ident))


def test_owner_role_with_workspace_guc_reads_only_its_rows(pg) -> None:
    conn, _ = pg
    with conn.cursor() as cur:
        _seed(cur)
        cur.execute("update reflect_memory.memory_items set metadata = "
                    "'{\"classification\":\"internal\"}'::jsonb where content_hash = 'hr';")
        cur.execute(M[3].read_text())
        cur.execute("insert into reflect_memory.ng_kv (workspace_id, namespace, key, value) values "
                    "(%s, 'full_docs', 'a', '{}'::jsonb), (%s, 'full_docs', 'b', '{}'::jsonb)", (WS_A, WS_B))
        with _owner_role(cur, "reflect_compat_owner"):
            cur.execute("set role reflect_compat_owner;")
            cur.execute("select count(*) as n from reflect_memory.memory_items")
            assert cur.fetchone()["n"] == 0
            cur.execute("select set_config('app.current_workspace', %s, false)", (WS_A,))
            cur.execute("select content from reflect_memory.memory_items order by content")
            contents = [r["content"] for r in cur.fetchall()]
            assert contents and all("tenant b" not in c for c in contents)
            cur.execute("select key from reflect_memory.ng_kv")
            assert [r["key"] for r in cur.fetchall()] == ["a"]
            cur.execute("select set_config('app.current_workspace', %s, false)", (WS_B,))
            cur.execute("select content from reflect_memory.memory_items")
            assert [r["content"] for r in cur.fetchall()] == ["tenant b auth token"]


def test_broker_path_reads_only_the_token_tenant(pg) -> None:
    conn, dsn = pg
    pytest.importorskip("fastapi")
    try:
        from reflect_kb.broker.app import create_app, psycopg_store_factory
        from reflect_kb.broker.auth import AuthError, Principal
    except ImportError:
        pytest.skip("broker lands in #39 (reflect_kb.broker not on this branch)")
    from fastapi.testclient import TestClient

    with conn.cursor() as cur:
        cur.execute(M[3].read_text())
        sha = "3f2a9c1d4e5b6a7f8091a2b3c4d5e6f708192a3b"
        for ws, content, h in ((WS_A, "tenant a auth token note", "ha"), (WS_B, "tenant b auth token note", "hb")):
            cur.execute(
                "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash, source_uri) "
                "values (%s, 'codebase_note', %s, %s, %s)", (ws, content, h, f"acme/widgets@{sha}:src/auth.rs"))

    class Verifier:
        def __init__(self, ws: str) -> None:
            self.ws = ws

        def verify(self, authorization):
            if not authorization:
                raise AuthError(401, "missing bearer token")
            return Principal(subject="compat", workspace_id=self.ws, claims={})

    class AcceptAll:
        def resolve(self, pin) -> bool:
            return True

    for ws, other in ((WS_A, "tenant b"), (WS_B, "tenant a")):
        app = create_app(verifier=Verifier(ws), store_factory=psycopg_store_factory(dsn), resolver=AcceptAll())
        r = TestClient(app).post("/v1/evidence", json={"query": "auth token", "workspace_id": other},
                                 headers={"Authorization": "Bearer x"})
        assert r.status_code == 200, r.text
        assert r.json()["workspace_id"] == ws
        assert other not in r.text
        assert len(r.json()["lexical"]) == 1
