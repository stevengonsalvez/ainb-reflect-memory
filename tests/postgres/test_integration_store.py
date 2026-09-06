# ABOUTME: Integration tests against a live Postgres (auto-skipped without one).
# ABOUTME: Proves the gates: insert/search FTS, idempotent ingestion, graph
# ABOUTME: neighborhood, tenant isolation, and RLS fail-closed direct access.

from __future__ import annotations

import re

import pytest

from reflect_kb.postgres import (
    EvidencePackQuery,
    InsertMemoryInput,
    SearchMemoryInput,
    Tenant,
    UpsertEdgeInput,
    UpsertEntityInput,
)

pytestmark = pytest.mark.integration

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"


# --------------------------------------------------------------------------- #
# insert + full-text search
# --------------------------------------------------------------------------- #


def test_insert_and_fts_search_returns_ranked_hit_with_snippet(store) -> None:
    a = Tenant(workspace_id=WS_A)
    item = store.insert_memory(
        InsertMemoryInput(
            tenant=a,
            content="The auth middleware token expiry uses a strict less-than check",
            source_type="codebase_note",
            source_uri="src/auth.rs",
        )
    )
    hits = store.search_memory(SearchMemoryInput(tenant=a, query="auth token expiry"))
    assert len(hits) == 1
    assert hits[0].item.id == item.id
    assert hits[0].rank > 0
    assert "<b>" in hits[0].snippet  # ts_headline highlighting present


def test_search_returns_nothing_for_unrelated_query(store) -> None:
    a = Tenant(workspace_id=WS_A)
    store.insert_memory(InsertMemoryInput(tenant=a, content="kubernetes pod scheduling notes"))
    assert store.search_memory(SearchMemoryInput(tenant=a, query="quantum chromodynamics")) == []


# --------------------------------------------------------------------------- #
# idempotent ingestion
# --------------------------------------------------------------------------- #


def test_insert_is_idempotent_per_normalized_content(store) -> None:
    a = Tenant(workspace_id=WS_A)
    first = store.insert_memory(
        InsertMemoryInput(tenant=a, content="Fixed the bug", confidence=0.5)
    )
    # same normalized content (case/whitespace folded) => same row, refreshed.
    second = store.insert_memory(
        InsertMemoryInput(tenant=a, content="  fixed   THE bug\n", confidence=0.9)
    )
    assert first.id == second.id
    assert second.confidence == pytest.approx(0.9)

    hits = store.search_memory(SearchMemoryInput(tenant=a, query="fixed bug"))
    assert len({h.item.id for h in hits}) == 1  # exactly one underlying row


def test_same_content_in_two_tenants_is_two_rows(store) -> None:
    # Dedupe is per-tenant: identical content in different workspaces coexists.
    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    ia = store.insert_memory(InsertMemoryInput(tenant=a, content="shared note text"))
    ib = store.insert_memory(InsertMemoryInput(tenant=b, content="shared note text"))
    assert ia.id != ib.id


# --------------------------------------------------------------------------- #
# entities + graph neighborhood
# --------------------------------------------------------------------------- #


def test_entity_alias_lookup(store) -> None:
    a = Tenant(workspace_id=WS_A)
    store.upsert_entity(
        UpsertEntityInput(
            tenant=a,
            canonical_name="JSON Web Token",
            entity_type="concept",
            aliases=["JWT", "jwt token"],
        )
    )
    hits = store.lookup_entities(a, "JWT")
    assert hits
    assert hits[0].canonical_name == "JSON Web Token"
    assert hits[0].matched_alias is not None


def test_upsert_entity_is_idempotent(store) -> None:
    a = Tenant(workspace_id=WS_A)
    first = store.upsert_entity(
        UpsertEntityInput(tenant=a, canonical_name="Auth", entity_type="component")
    )
    second = store.upsert_entity(
        UpsertEntityInput(
            tenant=a, canonical_name="Auth", entity_type="component", aliases=["authn"]
        )
    )
    assert first.id == second.id
    assert "authn" in second.aliases


def test_graph_neighborhood_is_same_tenant_only(store) -> None:
    a = Tenant(workspace_id=WS_A)
    auth = store.upsert_entity(
        UpsertEntityInput(tenant=a, canonical_name="Auth", entity_type="component")
    )
    jwt = store.upsert_entity(
        UpsertEntityInput(tenant=a, canonical_name="JWT", entity_type="concept")
    )
    store.upsert_edge(
        UpsertEdgeInput(
            tenant=a, source_entity_id=auth.id, target_entity_id=jwt.id, relation_type="uses"
        )
    )

    nb = store.neighborhood(a, auth.id, depth=1)
    assert len(nb.edges) == 1
    assert {e.canonical_name for e in nb.entities} == {"Auth", "JWT"}

    # A different tenant asking about A's entity id sees nothing.
    nb_b = store.neighborhood(Tenant(workspace_id=WS_B), auth.id, depth=1)
    assert nb_b.edges == []
    assert nb_b.entities == []


def test_every_call_ends_its_own_transaction_and_unbinds(conn, store) -> None:
    """On the documented non-autocommit connection every store call commits
    or rolls back its own transaction: the connection is idle afterwards, the
    tenant binding is gone, and a failed statement leaves it usable."""
    import psycopg

    idle = psycopg.pq.TransactionStatus.IDLE
    a = Tenant(workspace_id=WS_A)
    assert conn.info.transaction_status == idle
    store.insert_memory(InsertMemoryInput(tenant=a, content="idle after the write", source_type="note"))
    assert conn.info.transaction_status == idle
    assert store.search_memory(SearchMemoryInput(tenant=a, query="idle"))
    assert conn.info.transaction_status == idle
    with conn.transaction():
        row = conn.execute("select current_setting('app.current_workspace', true) as ws").fetchone()
    assert (row["ws"] or "") == "", row
    assert conn.info.transaction_status == idle
    ea = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="X", entity_type="t"))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.upsert_edge(UpsertEdgeInput(
            tenant=a, source_entity_id=ea.id, target_entity_id="00000000-0000-0000-0000-00000000dead",
            relation_type="rel"))
    assert conn.info.transaction_status == idle, "a failed call left the connection aborted"
    assert store.search_memory(SearchMemoryInput(tenant=a, query="idle")), "the connection is unusable after a failure"


def test_cross_tenant_edge_is_physically_rejected(store) -> None:
    import psycopg

    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    ea = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="X", entity_type="t"))
    eb = store.upsert_entity(UpsertEntityInput(tenant=b, canonical_name="Y", entity_type="t"))
    # Edge in workspace A pointing at B's entity violates the composite
    # (workspace_id, entity_id) FK — a cross-tenant edge cannot exist.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.upsert_edge(
            UpsertEdgeInput(
                tenant=a, source_entity_id=ea.id, target_entity_id=eb.id, relation_type="rel"
            )
        )


# --------------------------------------------------------------------------- #
# tenant isolation on the trusted (MemoryStore) path
# --------------------------------------------------------------------------- #


def test_search_is_tenant_scoped(store) -> None:
    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    store.insert_memory(InsertMemoryInput(tenant=a, content="alpha unique token zebra"))
    store.insert_memory(InsertMemoryInput(tenant=b, content="alpha unique token zebra"))

    hits_a = store.search_memory(SearchMemoryInput(tenant=a, query="zebra"))
    assert len(hits_a) == 1
    assert hits_a[0].item.workspace_id == WS_A


# --------------------------------------------------------------------------- #
# evidence pack — retrieval only, no synthesis
# --------------------------------------------------------------------------- #


def test_evidence_pack_assembles_lexical_entities_graph_citations(store) -> None:
    a = Tenant(workspace_id=WS_A)
    item = store.insert_memory(
        InsertMemoryInput(
            tenant=a,
            content="The auth middleware validates the token on every request",
            source_type="codebase_note",
            source_uri="src/auth.rs",
        )
    )
    auth = store.upsert_entity(
        UpsertEntityInput(
            tenant=a, canonical_name="auth", entity_type="component", aliases=["auth middleware"]
        )
    )
    token = store.upsert_entity(
        UpsertEntityInput(tenant=a, canonical_name="token", entity_type="concept")
    )
    store.upsert_edge(
        UpsertEdgeInput(
            tenant=a,
            source_entity_id=auth.id,
            target_entity_id=token.id,
            relation_type="validates",
            evidence_memory_id=item.id,
        )
    )

    pack = store.get_evidence_pack(EvidencePackQuery(tenant=a, query="auth"))
    assert pack.query == "auth"
    assert pack.tenant.workspace_id == WS_A
    assert any(h.memory_id == item.id for h in pack.lexical)
    assert any(e.canonical_name == "auth" for e in pack.entities)
    assert len(pack.graph.edges) >= 1
    assert any(c.memory_id == item.id for c in pack.citations)


# --------------------------------------------------------------------------- #
# Row-Level Security — the direct (PostgREST/JWT) access path, fail-closed
# --------------------------------------------------------------------------- #


def test_rls_isolates_direct_access_by_workspace_guc(conn, store) -> None:
    """An unprivileged role sees only its current-workspace rows, nothing
    without a workspace set. Exercises the RLS policies + tenant resolver GUC
    fallback that guard the direct Supabase/PostgREST client path."""
    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    store.insert_memory(InsertMemoryInput(tenant=a, content="alpha secret in A"))
    store.insert_memory(InsertMemoryInput(tenant=b, content="beta secret in B"))

    with conn.cursor() as cur:
        # Recreate a clean non-superuser role (RLS does not apply to owners).
        cur.execute(
            "do $$ begin "
            "  if exists (select 1 from pg_roles where rolname='reflect_rls_test') then "
            "    execute 'drop owned by reflect_rls_test'; "
            "    begin execute 'drop role reflect_rls_test'; exception when dependent_objects_still_exist then null; end; "
            "  end if; "
            "end $$;"
        )
        cur.execute("do $$ begin if not exists (select 1 from pg_roles where rolname='reflect_rls_test') then create role reflect_rls_test nologin; end if; end $$;")
        cur.execute("grant usage on schema reflect_memory to reflect_rls_test;")
        cur.execute("grant select on all tables in schema reflect_memory to reflect_rls_test;")
        cur.execute("grant execute on all functions in schema reflect_memory to reflect_rls_test;")
        conn.commit()

        cur.execute("select rolsuper, rolbypassrls from pg_roles where rolname = 'reflect_rls_test'")

        _r = cur.fetchone()

        assert _r["rolsuper"] is False and _r["rolbypassrls"] is False, "the RLS proof would be hollow"

        cur.execute("set role reflect_rls_test;")

        # MemoryStore binds app.current_workspace per call, so this connection
        # still carries the last tenant it wrote for; clear it to test the
        # unbound case.
        cur.execute("select set_config('app.current_workspace', '', false);")
        # No workspace resolvable -> resolver returns NULL -> deny all.
        cur.execute("select count(*) as n from reflect_memory.memory_items;")
        assert cur.fetchone()["n"] == 0

        # Scope to A via the GUC (set_config so it can be parameterized).
        cur.execute("select set_config('app.current_workspace', %s, false);", (WS_A,))
        cur.execute("select content from reflect_memory.memory_items order by content;")
        assert [r["content"] for r in cur.fetchall()] == ["alpha secret in A"]

        # Switch to B -> only B's row.
        cur.execute("select set_config('app.current_workspace', %s, false);", (WS_B,))
        cur.execute("select content from reflect_memory.memory_items order by content;")
        assert [r["content"] for r in cur.fetchall()] == ["beta secret in B"]

        cur.execute("reset role;")
    conn.commit()


# --------------------------------------------------------------------------- #
# Migration 0003: classification floor as a check constraint + FORCE RLS
# --------------------------------------------------------------------------- #


def test_check_constraint_refuses_restricted_and_pii_rows(conn) -> None:
    """The floor holds even for a client that bypasses the Python models."""
    import psycopg

    for label in ("restricted", "pii", "top-secret"):
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(
                "insert into reflect_memory.memory_items "
                "(workspace_id, source_type, content, content_hash, metadata) "
                "values (%s, 'note', %s, %s, %s::jsonb)",
                (WS_A, f"{label} row", f"hash-{label}", f'{{"classification": "{label}"}}'),
            )
        conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "insert into reflect_memory.memory_items "
            "(workspace_id, source_type, content, content_hash, metadata) "
            "values (%s, 'note', 'internal row', 'hash-internal', "
            "'{\"classification\": \"internal\"}'::jsonb)",
            (WS_A,),
        )
    conn.commit()


def test_rls_is_forced_so_the_table_owner_cannot_read_across_workspaces(conn, store) -> None:
    """ENABLE RLS exempts the table owner; FORCE does not. A non-superuser owner
    role sees nothing without a workspace and only its own workspace with one."""
    from psycopg import sql

    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    store.insert_memory(InsertMemoryInput(tenant=a, content="alpha owned in A"))
    store.insert_memory(InsertMemoryInput(tenant=b, content="beta owned in B"))

    with conn.cursor() as cur:
        cur.execute(
            "select tableowner from pg_tables "
            "where schemaname='reflect_memory' and tablename='memory_items'"
        )
        original_owner = cur.fetchone()["tableowner"]
        cur.execute(
            "select relforcerowsecurity from pg_class "
            "where oid = 'reflect_memory.memory_items'::regclass"
        )
        assert cur.fetchone()["relforcerowsecurity"] is True

        cur.execute(
            "do $$ begin "
            "  if exists (select 1 from pg_roles where rolname='reflect_owner_test') then "
            "    execute 'reassign owned by reflect_owner_test to current_user'; "
            "    execute 'drop owned by reflect_owner_test'; "
            "    execute 'drop role reflect_owner_test'; "
            "  end if; "
            "end $$;"
        )
        cur.execute("create role reflect_owner_test nologin;")
        cur.execute("grant usage on schema reflect_memory to reflect_owner_test;")
        cur.execute("grant execute on all functions in schema reflect_memory to reflect_owner_test;")
        cur.execute("alter table reflect_memory.memory_items owner to reflect_owner_test;")
        conn.commit()
        try:
            cur.execute("set role reflect_owner_test;")
            # MemoryStore binds app.current_workspace per call, so this connection
            # still carries the last tenant it wrote for; clear it to test the
            # unbound case.
            cur.execute("select set_config('app.current_workspace', '', false);")
            # The owner, with no workspace resolvable, sees nothing.
            cur.execute("select count(*) as n from reflect_memory.memory_items;")
            assert cur.fetchone()["n"] == 0
            # With workspace A set, the owner sees A only, never B.
            cur.execute("select set_config('app.current_workspace', %s, false);", (WS_A,))
            cur.execute("select content from reflect_memory.memory_items order by content;")
            assert [r["content"] for r in cur.fetchall()] == ["alpha owned in A"]
            cur.execute("reset role;")
        finally:
            cur.execute("reset role;")
            cur.execute(
                sql.SQL("alter table reflect_memory.memory_items owner to {};").format(
                    sql.Identifier(original_owner)
                )
            )
            cur.execute("drop owned by reflect_owner_test;")
            cur.execute("drop role reflect_owner_test;")
            conn.commit()


@pytest.mark.integration
def test_floor_constraint_refuses_restricted_rows_in_every_label_table(conn) -> None:
    """The check constraint from 0003 covers every table with a label column,
    so a restricted row cannot exist anywhere in the shared store even when a
    client bypasses the Python inputs."""
    import psycopg

    with conn.cursor() as cur:
        cur.execute("select conname from pg_constraint where conname like '%_classification_floor' order by 1")
        names = {r["conname"] for r in cur.fetchall()}
    # The three tables with a label column; the ng_* floor is engine-level.
    assert names == {f"{t}_classification_floor" for t in ("memory_items", "entities", "edges")}, names
    with conn.cursor() as cur:
        cur.execute("select convalidated from pg_constraint where conname = 'entities_classification_floor'")
        assert cur.fetchone()["convalidated"] is True
    restricted_entity = (
        "insert into reflect_memory.entities (workspace_id, canonical_name, entity_type, metadata) "
        "values (%s, 'Secret Component', 'component', '{\"classification\":\"restricted\"}'::jsonb)"
    )
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction(), conn.cursor() as cur:
        cur.execute(restricted_entity, (WS_A,))


@pytest.mark.integration
def test_store_binding_is_transaction_local_and_survives_a_rollback(conn, store) -> None:
    """The tenant is bound with SET LOCAL per call: a rollback (here a
    CheckViolation) does not leave a stale binding behind, the next call
    binds again and reads the tenant's rows, and after the call ends the GUC
    is unset at session scope so a pooled connection carries no workspace."""
    import psycopg

    a = Tenant(workspace_id=WS_A)
    store.insert_memory(InsertMemoryInput(tenant=a, content="alpha survives"))
    with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
        cur.execute(
            "insert into reflect_memory.entities (workspace_id, canonical_name, entity_type, metadata) "
            "values (%s, 'bad', 'component', '{\"classification\":\"pii\"}'::jsonb)", (WS_A,))
    conn.rollback()
    hits = store.search_memory(SearchMemoryInput(tenant=a, query="alpha"))
    assert [h.item.content for h in hits] == ["alpha survives"]
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("select coalesce(current_setting('app.current_workspace', true), '') as ws")
        assert cur.fetchone()["ws"] == "", "the binding leaked past the transaction"


@pytest.mark.integration
def test_0005_policies_use_an_initplan_and_the_resolver_has_no_handlers(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select polname, pg_get_expr(polqual, polrelid) as qual from pg_policy "
                    "where polname like '%_tenant_isolation' order by 1")
        rows = cur.fetchall()
        assert len(rows) == 7, rows
        for r in rows:
            assert re.search(r"\(\s*SELECT reflect_memory\.current_workspace_id\(\)", r["qual"]), r
        cur.execute("select pg_get_functiondef('reflect_memory.current_workspace_id()'::regprocedure) as def")
        body = cur.fetchone()["def"].lower()
        assert "exception" not in body and "missing_ok" not in body

# --------------------------------------------------------------------------- #
# Migration 0003: the floor inside the read functions, before LIMIT
# --------------------------------------------------------------------------- #


def test_read_functions_filter_entities_and_edges_above_the_floor(store) -> None:
    a = Tenant(workspace_id=WS_A)
    auth = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="auth", entity_type="component"))
    token = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="token", entity_type="concept"))
    vault = store.upsert_entity(
        UpsertEntityInput(tenant=a, canonical_name="vault secret", entity_type="concept",
                          metadata={"classification": "restricted"})
    )
    store.upsert_edge(UpsertEdgeInput(tenant=a, source_entity_id=auth.id, target_entity_id=token.id,
                                      relation_type="validates"))
    store.upsert_edge(UpsertEdgeInput(tenant=a, source_entity_id=auth.id, target_entity_id=vault.id,
                                      relation_type="reads", metadata={"classification": "pii"}))
    # A restricted entity never comes back from a lookup, even when it matches.
    assert [h.canonical_name for h in store.lookup_entities(a, "vault")] == []
    assert [h.canonical_name for h in store.lookup_entities(a, "auth")] == ["auth"]
    # A pii edge is not walked, so its endpoint is not hydrated either.
    nb = store.neighborhood(a, auth.id, depth=2)
    assert {e.relation_type for e in nb.edges} == {"validates"}
    assert {e.canonical_name for e in nb.entities} == {"auth", "token"}


def test_bind_workspace_scopes_a_non_bypass_role_through_rls(conn, store) -> None:
    """The broker binds app.current_workspace inside the request transaction;
    a role subject to RLS then reads only that workspace through the store."""
    a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
    store.insert_memory(InsertMemoryInput(tenant=a, content="alpha bound row"))
    store.insert_memory(InsertMemoryInput(tenant=b, content="beta bound row"))
    with conn.cursor() as cur:
        cur.execute(
            "do $$ begin if not exists (select 1 from pg_roles where rolname='reflect_bind_test') "
            "then create role reflect_bind_test nologin; end if; end $$;")
        cur.execute("grant usage on schema reflect_memory to reflect_bind_test;")
        cur.execute("grant select on all tables in schema reflect_memory to reflect_bind_test;")
        cur.execute("grant execute on all functions in schema reflect_memory to reflect_bind_test;")
        conn.commit()
        cur.execute("set role reflect_bind_test;")
        try:
            store.bind_workspace(WS_A)
            # Even a query built for B returns nothing: RLS sees workspace A only.
            assert store.search_memory(SearchMemoryInput(tenant=b, query="bound")) == []
            hits = store.search_memory(SearchMemoryInput(tenant=a, query="bound"))
            assert [h.item.content for h in hits] == ["alpha bound row"]
        finally:
            cur.execute("reset role;")
            conn.rollback()
