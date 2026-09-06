"""Gate 4: Mode 2 migration proof on a disposable Postgres.

Seed legacy rows with no label plus one restricted row, apply the migrations
in order, assert the readable error names the row count, relabel, re-apply,
assert FORCE on all seven tables, then prove, as NON-superuser roles created
in the disposable database (pg_roles.rolsuper asserted false):

- an owner LOGIN role writes through MemoryStore and reads back its own rows
  and no other workspace's (the compat break for existing Mode 2 users:
  FORCE RLS refuses an unbound owner write);
- scripts/seed.py runs as that owner role;
- the broker path, as a non-owner reader role, serves only the token tenant;
- the broker's startup check refuses superuser, BYPASSRLS and owner roles.

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
ALL_MIGRATIONS = sorted(MIGRATIONS.glob("*.sql"))   # applied in name order, every file
ROLE_PASSWORD = "compat-role-password"
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


def _apply_all(cur) -> None:
    """Every migration in name order, aborting on the first failure."""
    for path in ALL_MIGRATIONS:
        cur.execute(path.read_text())


def _dsn_as(dsn: str, user: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(dsn)
    parts.update(user=user, password=ROLE_PASSWORD)
    return make_conninfo(**parts)


def _assert_not_superuser(cur) -> None:
    cur.execute("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
    row = cur.fetchone()
    assert row["rolsuper"] is False and row["rolbypassrls"] is False, (
        f"the role under test bypasses RLS ({row}); the proof would be hollow")


@contextmanager
def _login_role(cur, role: str, *, owner: bool):
    """A NOSUPERUSER NOBYPASSRLS LOGIN role: the table owner (the documented
    worker DSN) or a reader with SELECT plus EXECUTE (the documented broker DSN)."""
    from psycopg import sql

    ident = sql.Identifier(role)
    cur.execute(sql.SQL(
        "do $$ begin if exists (select 1 from pg_roles where rolname = {lit}) then "
        "execute format('reassign owned by %I to current_user', {lit}); "
        "execute format('drop owned by %I', {lit}); execute format('drop role %I', {lit}); "
        "end if; end $$;").format(lit=sql.Literal(role)))
    cur.execute(sql.SQL("create role {} login nosuperuser nobypassrls password {};").format(
        ident, sql.Literal(ROLE_PASSWORD)))
    cur.execute(sql.SQL("grant usage on schema reflect_memory to {};").format(ident))
    cur.execute(sql.SQL("grant execute on all functions in schema reflect_memory to {};").format(ident))
    cur.execute(sql.SQL("grant select on all tables in schema reflect_memory to {};").format(ident))
    cur.execute("select tableowner from pg_tables where schemaname='reflect_memory' and tablename='memory_items'")
    original = cur.fetchone()["tableowner"]
    if owner:
        for t in SEVEN:
            cur.execute(sql.SQL("alter table reflect_memory.{} owner to {}").format(sql.Identifier(t), ident))
        cur.execute(sql.SQL("grant usage on all sequences in schema reflect_memory to {};").format(ident))
    try:
        yield
    finally:
        if owner:
            for t in SEVEN:
                cur.execute(sql.SQL("alter table reflect_memory.{} owner to {}").format(
                    sql.Identifier(t), sql.Identifier(original)))
        cur.execute(sql.SQL("drop owned by {r}; drop role {r};").format(r=ident))


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
        _apply_all(cur)
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
    cur.execute("select rolsuper from pg_roles where rolname = %s", (role,))
    assert cur.fetchone()["rolsuper"] is False
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
    """The broker connects as a non-owner reader (SELECT plus EXECUTE, no
    BYPASSRLS), not as the admin: FORCE RLS is the layer under its scoping."""
    conn, dsn = pg
    pytest.importorskip("fastapi")
    try:
        from reflect_kb.broker.app import create_app, psycopg_store_factory
        from reflect_kb.broker.auth import AuthError, Principal
    except ImportError:
        pytest.skip("broker lands in #39 (reflect_kb.broker not on this branch)")
    from fastapi.testclient import TestClient

    with conn.cursor() as cur:
        _apply_all(cur)
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

    with conn.cursor() as cur, _login_role(cur, "reflect_compat_reader", owner=False):
        reader_dsn = _dsn_as(dsn, "reflect_compat_reader")
        probe = connect_or_skip(reader_dsn, row_factory=__import__("psycopg.rows", fromlist=["dict_row"]).dict_row)
        with probe.cursor() as pc:
            _assert_not_superuser(pc)
        probe.close()
        for ws, other in ((WS_A, "tenant b"), (WS_B, "tenant a")):
            app = create_app(verifier=Verifier(ws), store_factory=psycopg_store_factory(reader_dsn), resolver=AcceptAll())
            r = TestClient(app).post("/v1/evidence", json={"query": "auth token", "workspace_id": other},
                                     headers={"Authorization": "Bearer x"})
            assert r.status_code == 200, r.text
            assert r.json()["workspace_id"] == ws
            assert other not in r.text
            assert len(r.json()["lexical"]) == 1


def test_owner_login_dsn_writes_and_reads_back_through_memory_store(pg) -> None:
    """The documented worker DSN (table owner, not BYPASSRLS) must still write
    under FORCE RLS: MemoryStore binds the tenant it writes for. Before the
    fix every insert fails with "new row violates row-level security policy"."""
    conn, dsn = pg
    from psycopg.rows import dict_row

    from reflect_kb.postgres import (
        InsertMemoryInput,
        MemoryStore,
        SearchMemoryInput,
        Tenant,
        UpsertEdgeInput,
        UpsertEntityInput,
    )

    with conn.cursor() as cur:
        _apply_all(cur)
    with conn.cursor() as cur, _login_role(cur, "reflect_compat_owner_login", owner=True):
        owner = connect_or_skip(_dsn_as(dsn, "reflect_compat_owner_login"), row_factory=dict_row, autocommit=False)
        try:
            with owner.cursor() as oc:
                _assert_not_superuser(oc)
            store = MemoryStore(owner)
            for ws, text in ((WS_A, "tenant a jwt expiry note"), (WS_B, "tenant b jwt expiry note")):
                tenant = Tenant(workspace_id=ws, agent_id=None)
                note = store.insert_memory(InsertMemoryInput(tenant=tenant, content=text, source_type="note"))
                e1 = store.upsert_entity(UpsertEntityInput(tenant=tenant, canonical_name=f"JWT {ws[:2]}", entity_type="concept"))
                e2 = store.upsert_entity(UpsertEntityInput(tenant=tenant, canonical_name=f"Auth {ws[:2]}", entity_type="component"))
                store.upsert_edge(UpsertEdgeInput(tenant=tenant, source_entity_id=e2.id, target_entity_id=e1.id,
                                                  relation_type="validates", evidence_memory_id=note.id))
            hits_a = store.search_memory(SearchMemoryInput(tenant=Tenant(workspace_id=WS_A, agent_id=None), query="jwt expiry"))
            assert [h.item.content for h in hits_a] == ["tenant a jwt expiry note"]
            hits_b = store.search_memory(SearchMemoryInput(tenant=Tenant(workspace_id=WS_B, agent_id=None), query="jwt expiry"))
            assert [h.item.content for h in hits_b] == ["tenant b jwt expiry note"]
            # Raw read under the last binding sees one workspace only.
            with owner.cursor() as oc:
                oc.execute("select set_config('app.current_workspace', %s, false)", (WS_A,))
                oc.execute("select content from reflect_memory.memory_items")
                assert [r["content"] for r in oc.fetchall()] == ["tenant a jwt expiry note"]
                oc.execute("select count(*) as n from reflect_memory.entities")
                assert oc.fetchone()["n"] == 2
        finally:
            owner.close()


def test_seed_script_runs_as_the_owner_role(pg) -> None:
    """scripts/seed.py is the documented smoke test; it must work on the
    documented worker DSN, so it binds the workspace before its first insert."""
    import os
    import subprocess
    import sys

    conn, dsn = pg
    with conn.cursor() as cur:
        _apply_all(cur)
    with conn.cursor() as cur, _login_role(cur, "reflect_compat_seed_owner", owner=True):
        env = {**os.environ, "DATABASE_URL": _dsn_as(dsn, "reflect_compat_seed_owner"),
               "PYTHONPATH": str(REPO / "src"), "REFLECT_PG_ALLOW_INSECURE": "1"}
        proc = subprocess.run([sys.executable, str(REPO / "scripts" / "seed.py"), WS_A], env=env,
                              capture_output=True, text=True, timeout=300, check=False)
        assert proc.returncode == 0, f"seed.py failed as the owner role:\n{proc.stdout[-800:]}\n{proc.stderr[-1500:]}"
        assert "lexical hits" in proc.stdout
        cur.execute("select count(*) as n from reflect_memory.memory_items where workspace_id = %s", (WS_A,))
        assert cur.fetchone()["n"] >= 2


def test_broker_startup_refuses_roles_that_bypass_rls(pg) -> None:
    """The broker's DSN must be a role RLS applies to: superuser, BYPASSRLS
    and the table owner are refused at startup; the reader passes."""
    conn, dsn = pg
    try:
        from reflect_kb.broker.config import assert_broker_role
    except ImportError:
        pytest.skip("broker role check lands in #39 (reflect_kb.broker.config.assert_broker_role)")
    with conn.cursor() as cur:
        _apply_all(cur)
        cur.execute("select rolsuper from pg_roles where rolname = current_user")
        admin_is_super = cur.fetchone()["rolsuper"]
    if admin_is_super:
        with pytest.raises(RuntimeError, match="superuser"):
            assert_broker_role(dsn)
    with conn.cursor() as cur, _login_role(cur, "reflect_compat_owner_login", owner=True):
        with pytest.raises(RuntimeError, match="own"):
            assert_broker_role(_dsn_as(dsn, "reflect_compat_owner_login"))
    with conn.cursor() as cur, _login_role(cur, "reflect_compat_reader", owner=False):
        assert_broker_role(_dsn_as(dsn, "reflect_compat_reader"))
        cur.execute("alter role reflect_compat_reader bypassrls")
        with pytest.raises(RuntimeError, match="BYPASSRLS"):
            assert_broker_role(_dsn_as(dsn, "reflect_compat_reader"))


def test_0004_creates_the_broker_and_writer_roles(pg) -> None:
    """Migration 0004 creates reflect_broker (SELECT plus EXECUTE only, never
    owner) and reflect_writer (DML under RLS) NOLOGIN, with no secret in the
    file; scripts/provision_roles.py then gives them LOGIN passwords from the
    environment. Re-running 0004 is a no-op, and it never re-issues an
    attribute that is already right (a CREATEROLE-only migrator can run it)."""
    import os
    import subprocess
    import sys

    conn, dsn = pg
    if not (MIGRATIONS / "0004_broker_and_writer_roles.sql").exists():
        pytest.skip("0004 lands in #39")
    with conn.cursor() as cur:
        # Roles are cluster objects: a previous run's provisioning survives the
        # disposable database. Reset the attribute the migration must not
        # set, so the assertion below is about the migration, not history.
        cur.execute("do $$ begin "
                    "if exists (select 1 from pg_roles where rolname = 'reflect_broker') then alter role reflect_broker nologin; end if; "
                    "if exists (select 1 from pg_roles where rolname = 'reflect_writer') then alter role reflect_writer nologin; end if; "
                    "end $$;")
        _apply_all(cur)
        cur.execute("select rolname, rolcanlogin, rolsuper, rolbypassrls from pg_roles "
                    "where rolname in ('reflect_broker', 'reflect_writer') order by rolname")
        roles = {r["rolname"]: r for r in cur.fetchall()}
        assert set(roles) == {"reflect_broker", "reflect_writer"}, roles
        for r in roles.values():
            assert not r["rolcanlogin"] and not r["rolsuper"] and not r["rolbypassrls"], r
        cur.execute("select count(*) as n from pg_tables where schemaname = 'reflect_memory' "
                    "and tableowner in ('reflect_broker', 'reflect_writer')")
        assert cur.fetchone()["n"] == 0
        cur.execute("select has_table_privilege('reflect_broker', 'reflect_memory.memory_items', 'INSERT') as w, "
                    "has_table_privilege('reflect_broker', 'reflect_memory.memory_items', 'SELECT') as r, "
                    "has_table_privilege('reflect_writer', 'reflect_memory.memory_items', 'INSERT') as ww")
        priv = cur.fetchone()
        assert priv["r"] and not priv["w"] and priv["ww"], priv
        _apply_all(cur)  # re-runnable
    env = {**os.environ, "DATABASE_URL": dsn, "REFLECT_BROKER_PASSWORD": ROLE_PASSWORD,
           "REFLECT_WRITER_PASSWORD": ROLE_PASSWORD, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "provision_roles.py")], env=env,
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[-800:]
    from psycopg.rows import dict_row

    from reflect_kb.postgres import InsertMemoryInput, MemoryStore, Tenant

    writer = connect_or_skip(_dsn_as(dsn, "reflect_writer"), row_factory=dict_row, autocommit=False)
    try:
        with writer.cursor() as wc:
            _assert_not_superuser(wc)
        item = MemoryStore(writer).insert_memory(InsertMemoryInput(
            tenant=Tenant(workspace_id=WS_A, agent_id=None), content="written by reflect_writer", source_type="note"))
        assert item.workspace_id == WS_A
    finally:
        writer.close()


def test_capture_pipeline_serves_a_pinned_hit_without_the_seeder(pg, tmp_path, reflect_bin) -> None:
    """Mode 2 end to end with no seeder: the migrations and the provisioned
    reflect_writer role, then `reflect add` on a note carrying repo, commit
    and source_path, then the broker as reflect_broker serves that hit with
    its pin resolved against a local checkout."""
    import os
    import subprocess
    import sys

    conn, dsn = pg
    if not (MIGRATIONS / "0004_broker_and_writer_roles.sql").exists():
        pytest.skip("0004 lands in #39")
    try:
        from reflect_kb.broker.app import create_app, psycopg_store_factory
        from reflect_kb.broker.auth import AuthError, Principal
        from reflect_kb.broker.pinning import LocalGitResolver
    except ImportError:
        pytest.skip("broker lands in #39")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from .conftest import REPO as CHECKOUT

    with conn.cursor() as cur:
        _apply_all(cur)
    env_admin = {**os.environ, "DATABASE_URL": dsn, "REFLECT_BROKER_PASSWORD": ROLE_PASSWORD,
                 "REFLECT_WRITER_PASSWORD": ROLE_PASSWORD, "PYTHONPATH": str(CHECKOUT / "src")}
    assert subprocess.run([sys.executable, str(CHECKOUT / "scripts" / "provision_roles.py")], env=env_admin,
                          capture_output=True, text=True, timeout=120, check=False).returncode == 0

    # A tiny checkout the pin points at.
    repo = tmp_path / "widgets"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "src").mkdir()
    (repo / "src" / "auth.rs").write_text("fn validate_jwt() {}\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "auth"], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    note = tmp_path / "jwt-expiry.md"
    note.write_text(
        "---\ntitle: JWT expiry uses a strict less-than\ncategory: auth\nkey_insight: the expiry check must be inclusive\n"
        f"classification: internal\nrepo: acme/widgets\ncommit: {sha}\nsource_path: src/auth.rs\n---\n\n"
        "The auth middleware validates the JWT token expiry with a strict less-than check.\n", encoding="utf-8")
    env = {**os.environ, "GLOBAL_LEARNINGS_PATH": str(tmp_path / "kb"), "REFLECT_STATE_DIR": str(tmp_path / "state"),
           "REFLECT_PG_DSN": _dsn_as(dsn, "reflect_writer"), "REFLECT_WORKSPACE_ID": WS_A,
           "REFLECT_PG_ALLOW_INSECURE": "1", "REFLECT_NO_DAEMON": "1"}
    proc = subprocess.run([str(reflect_bin), "add", "--force", str(note)], env=env, capture_output=True,
                          text=True, timeout=900, check=False)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    with conn.cursor() as cur:
        cur.execute("select source_uri, metadata->>'classification' as label from reflect_memory.memory_items "
                    "where workspace_id = %s", (WS_A,))
        rows = cur.fetchall()
    assert [(r["source_uri"], r["label"]) for r in rows] == [(f"acme/widgets@{sha}:src/auth.rs", "internal")], (
        rows, proc.stdout[-600:], proc.stderr[-600:])

    class Verifier:
        def verify(self, authorization):
            if not authorization:
                raise AuthError(401, "missing bearer token")
            return Principal(subject="compat", workspace_id=WS_A, claims={})

    with conn.cursor() as cur, _login_role(cur, "reflect_compat_reader", owner=False):
        app = create_app(verifier=Verifier(), store_factory=psycopg_store_factory(_dsn_as(dsn, "reflect_compat_reader")),
                         resolver=LocalGitResolver({"acme/widgets": repo}))
        r = TestClient(app).post("/v1/evidence", json={"query": "JWT expiry"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lexical"], body
    assert body["lexical"][0]["source_uri"] == f"acme/widgets@{sha}:src/auth.rs"
    assert body["meta"]["dropped"] == {} or not any(body["meta"]["dropped"].values()), body["meta"]

