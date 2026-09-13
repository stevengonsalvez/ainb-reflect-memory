"""The read functions after every migration: no wildcard enumeration, the
floor composed into every entity-returning query, and an index-friendly
neighbourhood."""

from __future__ import annotations

import pytest

from reflect_kb.postgres import (
    InsertMemoryInput,
    Tenant,
    UpsertEdgeInput,
    UpsertEntityInput,
)
from reflect_kb.postgres import sql as sqlb

pytestmark = pytest.mark.integration

WS = "0aaaaaaa-0000-4000-8000-00000000aaaa"


def _sql(conn, text, params=()):
    with conn.cursor() as cur:
        cur.execute(text, params)
        return cur.fetchall()


def test_a_wildcard_query_is_not_an_enumeration_oracle(conn, store) -> None:
    a = Tenant(workspace_id=WS)
    for name in ("auth middleware", "token store", "deploy script"):
        store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name=name, entity_type="component"))
    assert _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "%")) == []
    assert _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "_")) == []
    # Wildcards around a word are characters: the word still fuzzy-matches its
    # own entity, the wildcards match nothing else.
    rows = _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "%auth%"))
    assert [r["canonical_name"] for r in rows] == ["auth middleware"]
    # A literal percent sign in a name is matched by a literal percent sign.
    store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="100% coverage", entity_type="goal"))
    rows = _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "100%"))
    assert [r["canonical_name"] for r in rows] == ["100% coverage"]
    rows = _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "token"))
    assert [r["canonical_name"] for r in rows] == ["token store"]


def test_restricted_entity_is_never_hydrated_for_an_internal_edge(conn, store) -> None:
    a = Tenant(workspace_id=WS)
    auth = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="auth", entity_type="component"))
    with conn.cursor() as cur:
        # A restricted entity can only exist from before 0003; plant one past the constraint.
        cur.execute("alter table reflect_memory.entities drop constraint if exists entities_classification_floor")
        cur.execute(
            "insert into reflect_memory.entities (workspace_id, canonical_name, entity_type, aliases, metadata) "
            "values (%s, 'vault secret', 'secret', '{}', '{\"classification\":\"restricted\"}'::jsonb) returning id",
            (WS,))
        vault_id = str(cur.fetchone()["id"])
    conn.commit()
    try:
        store.upsert_edge(UpsertEdgeInput(tenant=a, source_entity_id=auth.id, target_entity_id=vault_id,
                                          relation_type="reads"))
        rows = _sql(conn, *sqlb.entities_by_ids(a, [auth.id, vault_id]))
        assert [r["canonical_name"] for r in rows] == ["auth"]
        assert _sql(conn, "select canonical_name from reflect_memory.search_entities(%s, %s, 10)", (WS, "vault")) == []
        nb = store.neighborhood(a, auth.id, depth=1)
        assert {e.canonical_name for e in nb.entities} == {"auth"}
    finally:
        with conn.cursor() as cur:
            cur.execute("delete from reflect_memory.edges where workspace_id = %s", (WS,))
            cur.execute("delete from reflect_memory.entities where workspace_id = %s", (WS,))
            cur.execute("alter table reflect_memory.entities add constraint entities_classification_floor "
                        "check (metadata->>'classification' is null or metadata->>'classification' in ('public', 'internal'))")
        conn.commit()


def test_neighborhood_uses_the_edge_indexes(conn, store) -> None:
    """With sequential scans disabled, a query that can use
    edges_workspace_source_idx and edges_workspace_target_idx shows index or
    bitmap index scans; one that cannot still shows a Seq Scan on edges."""
    a = Tenant(workspace_id=WS)
    auth = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="auth", entity_type="component"))
    tok = store.upsert_entity(UpsertEntityInput(tenant=a, canonical_name="token", entity_type="concept"))
    mem = store.insert_memory(InsertMemoryInput(tenant=a, content="auth validates the token"))
    store.upsert_edge(UpsertEdgeInput(tenant=a, source_entity_id=auth.id, target_entity_id=tok.id,
                                      relation_type="validates", evidence_memory_id=mem.id))
    body = _sql(conn, "select pg_get_functiondef('reflect_memory.entity_neighborhood(uuid, uuid, int)'::regprocedure) as d")[0]["d"]
    assert "shareable_edges" not in body and "left join reflect_memory.memory_items" in body
    with conn.cursor() as cur:
        cur.execute("set local enable_seqscan = off")
        cur.execute("explain (format text) select * from reflect_memory.entity_neighborhood(%s, %s, 2)", (WS, auth.id))
        plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
    conn.rollback()
    if "Function Scan" in plan and "edges" not in plan:
        # The planner did not inline the function; explain the body directly.
        inner = body.split("AS $function$", 1)[1].rsplit("$function$", 1)[0]
        inner = inner.replace("p_workspace_id", "%(ws)s::uuid").replace("p_entity_id", "%(eid)s::uuid").replace("p_max_depth", "2")
        with conn.cursor() as cur:
            cur.execute("set local enable_seqscan = off")
            cur.execute("explain (format text) " + inner, {"ws": WS, "eid": auth.id})
            plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
        conn.rollback()
    assert "Seq Scan on edges" not in plan, plan
    assert "edges_workspace_source_idx" in plan or "edges_workspace_target_idx" in plan, plan
