"""The /v1/evidence route end to end against a canned store and a real git repo."""

from __future__ import annotations

from .conftest import REPO, WS_A, WS_B


def _auth(issuer, **kw) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.mint(**kw)}"}


def test_every_returned_hit_is_pinned_and_resolved(client, issuer, git_repo) -> None:
    _, sha = git_repo
    r = client.post("/v1/evidence", json={"query": "auth"}, headers=_auth(issuer))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == WS_A
    ids = [h["memory_id"] for h in body["lexical"]]
    assert ids == ["pinned-ok", "pinned-short-sha", "public"]
    for h in body["lexical"]:
        assert h["source_uri"].startswith(f"{REPO}@{sha[:12]}")
        assert h["source"]["repo"] == REPO
        assert h["source"]["path"] == "src/auth.rs"
    assert body["lexical"][0]["source"]["line_start"] == 3
    assert body["lexical"][0]["source"]["line_end"] == 9
    # Citations only for hits that survived.
    assert sorted(c["memory_id"] for c in body["citations"]) == sorted(ids)
    # Every refusal is counted, as one total: 7 hits (2 unpinned, 3
    # unresolvable, 2 classified), 1 edge citing a refused hit, 1 restricted
    # graph entity. Edges and entities merely unlinked to a kept hit are
    # omitted without counting.
    assert body["meta"] == {"returned": 3, "dropped": {"total": 9}, "evidence_only": True}
    # Graph edges survive only when their evidence memory is a kept hit; an
    # edge citing no memory has no pin and goes too.
    assert [e["id"] for e in body["graph"]["edges"]] == ["edge-kept"]
    assert "bad-sha" not in r.text and "never-a-hit" not in r.text
    assert [e["canonical_name"] for e in body["graph"]["entities"]] == ["auth", "token"]
    # Restricted and pii never appear anywhere in the payload.
    assert "restricted" not in r.text
    assert "content of pii" not in r.text


def test_no_token_is_401(client) -> None:
    r = client.post("/v1/evidence", json={"query": "auth"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_without_workspace_claim_is_403(client, issuer, fake_store) -> None:
    r = client.post(
        "/v1/evidence", json={"query": "auth"}, headers=_auth(issuer, workspace_id=None)
    )
    assert r.status_code == 403
    assert fake_store.queries == []  # the store is never consulted


def test_bad_signature_is_401(client, issuer, fake_store) -> None:
    r = client.post("/v1/evidence", json={"query": "auth"}, headers=_auth(issuer, rogue=True))
    assert r.status_code == 401
    assert fake_store.queries == []


def test_body_supplied_tenant_is_ignored(client, issuer, fake_store) -> None:
    r = client.post(
        "/v1/evidence",
        json={
            "query": "auth",
            "workspace_id": WS_B,
            "tenant": {"workspace_id": WS_B},
            "tenant_id": WS_B,
        },
        headers=_auth(issuer),
    )
    assert r.status_code == 200
    assert r.json()["workspace_id"] == WS_A
    assert [q.tenant.workspace_id for q in fake_store.queries] == [WS_A]



def test_query_string_tenant_is_ignored_on_get(client, issuer, fake_store) -> None:
    r = client.get(
        "/v1/evidence", params={"q": "auth", "workspace_id": WS_B}, headers=_auth(issuer)
    )
    assert r.status_code == 200
    assert r.json()["workspace_id"] == WS_A
    assert fake_store.queries[-1].tenant.workspace_id == WS_A
    assert fake_store.queries[-1].query == "auth"


def test_limits_are_capped_and_validated(client, issuer, fake_store) -> None:
    r = client.post(
        "/v1/evidence", json={"query": "auth", "lexical_limit": 10_000}, headers=_auth(issuer)
    )
    assert r.status_code == 200
    assert fake_store.queries[-1].lexical_limit == 50
    r = client.post("/v1/evidence", json={"query": ""}, headers=_auth(issuer))
    assert r.status_code == 422
    r = client.post("/v1/evidence", json={"query": "x", "neighborhood_depth": 9}, headers=_auth(issuer))
    assert r.status_code == 422


def test_response_is_evidence_only(client, issuer) -> None:
    body = client.post("/v1/evidence", json={"query": "auth"}, headers=_auth(issuer)).json()
    assert set(body) == {"query", "workspace_id", "lexical", "entities", "graph", "citations", "meta"}
    assert "answer" not in body and "summary" not in body


def test_health_needs_no_token(client) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def _graph_pack(hits, *, edges, entity_hits):
    from datetime import UTC, datetime

    from reflect_kb.postgres import Edge, Entity, EntityHit, EvidencePack, GraphNeighborhood, Tenant

    now = datetime.now(UTC)
    tenant = Tenant(workspace_id=WS_A)
    names = {"e-auth": ("auth", ("authn",)), "e-token": ("token", ()), "e-db": ("db", ("postgres",))}
    return EvidencePack(
        query="auth",
        tenant=tenant,
        lexical=hits,
        entities=[EntityHit(eid, name, "component", aliases[0] if aliases else None) for eid, (name, aliases) in names.items()
                  if eid in entity_hits],
        graph=GraphNeighborhood(
            entities=[Entity(eid, WS_A, name, "component", aliases, {}, now, now) for eid, (name, aliases) in names.items()],
            edges=[Edge(eid, WS_A, src, dst, rel, ev, 1.0, {}, now, now) for eid, src, dst, rel, ev in edges],
        ),
        citations=[],
    )


def test_unpinned_hit_carries_out_no_entity_alias_or_edge(resolver) -> None:
    """With zero pinned hits, nothing from the graph or entity search may
    leave: no names, no aliases, no edge citing no memory, no edge citing the
    refused hit."""
    from reflect_kb.broker.app import filter_pack

    from .conftest import hit

    pack = _graph_pack(
        [hit("unpinned", "src/auth.rs")],
        edges=[
            ("edge-null", "e-auth", "e-db", "migrates_to", None),
            ("edge-unpinned", "e-auth", "e-token", "validates", "unpinned"),
        ],
        entity_hits={"e-auth", "e-db"},
    )
    out = filter_pack(pack, resolver)
    text = out.model_dump_json()
    assert out.lexical == [] and out.entities == []
    assert out.graph.entities == [] and out.graph.edges == []
    for leaked in ("auth", "authn", "postgres", "migrates_to", "validates"):
        assert leaked not in text.replace('"query":"auth"', ""), leaked
    # The refused hit and the edge citing it; the unlinked rest is omitted uncounted.
    assert out.meta.dropped.total == 2


def test_entities_survive_only_through_an_edge_citing_a_kept_hit(resolver, git_repo) -> None:
    from reflect_kb.broker.app import filter_pack

    from .conftest import hit

    _, sha = git_repo
    pack = _graph_pack(
        [hit("pinned", f"{REPO}@{sha}:src/auth.rs")],
        edges=[
            ("edge-kept", "e-auth", "e-token", "validates", "pinned"),
            ("edge-null", "e-auth", "e-db", "migrates_to", None),
        ],
        entity_hits={"e-auth", "e-db"},
    )
    out = filter_pack(pack, resolver)
    assert [h.memory_id for h in out.lexical] == ["pinned"]
    assert [e.id for e in out.graph.edges] == ["edge-kept"]
    assert sorted(e.id for e in out.graph.entities) == ["e-auth", "e-token"]
    assert [e.entity_id for e in out.entities] == ["e-auth"]  # e-db only rode on the null edge
    assert "postgres" not in out.model_dump_json()
    assert out.meta.dropped.total == 0  # nothing was refused, only unlinked context omitted


def test_drop_reasons_are_logged_not_returned(client, issuer, caplog) -> None:
    """Per-reason counts would tell a tenant whether a pin to some other repo
    resolves; the response carries one total, the log keeps the split."""
    with caplog.at_level("INFO", logger="reflect_kb.broker.app"):
        r = client.post("/v1/evidence", json={"query": "auth"}, headers=_auth(issuer))
    dropped = r.json()["meta"]["dropped"]
    assert dropped == {"total": 9}
    for reason in ("unpinned", "unresolvable", "classified", "unverified"):
        assert reason not in r.text, reason
    assert "unpinned=2 unresolvable=3" in caplog.text


def test_classified_edge_is_refused_even_with_kept_evidence(resolver, git_repo) -> None:
    """An edge labelled above the floor that predates the constraint must not
    ride out on a kept hit; SQL filters it too, this is the broker's own check."""
    import dataclasses

    from reflect_kb.broker.app import filter_pack

    from .conftest import hit

    _, sha = git_repo
    pack = _graph_pack(
        [hit("pinned", f"{REPO}@{sha}:src/auth.rs")],
        edges=[("edge-secret", "e-auth", "e-token", "reads_secret", "pinned")],
        entity_hits=set(),
    )
    secret = dataclasses.replace(pack.graph.edges[0], metadata={"classification": "restricted"})
    pack = dataclasses.replace(pack, graph=dataclasses.replace(pack.graph, edges=[secret]))
    out = filter_pack(pack, resolver)
    assert out.graph.edges == [] and out.graph.entities == []
    assert "reads_secret" not in out.model_dump_json()
    assert out.meta.dropped.total == 1
