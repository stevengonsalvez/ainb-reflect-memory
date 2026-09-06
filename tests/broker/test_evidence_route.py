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
    # Every refusal is counted.
    assert body["meta"] == {
        "returned": 3,
        # classified counts the restricted graph entity too; the edge that
        # touched it is dropped with the edges whose evidence was refused.
        "dropped": {"unpinned": 2, "unresolvable": 3, "classified": 3, "unverified_edges": 3},
        "evidence_only": True,
    }
    # Graph edges survive only when their evidence memory did (or they cite none).
    assert [e["id"] for e in body["graph"]["edges"]] == ["edge-kept", "edge-no-evidence"]
    assert "bad-sha" not in r.text and "never-a-hit" not in r.text
    assert [e["canonical_name"] for e in body["graph"]["entities"]] == ["auth", "token"]
    # Restricted and pii never appear anywhere in the payload.
    assert "restricted" not in r.text.replace('"classified"', "")
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
