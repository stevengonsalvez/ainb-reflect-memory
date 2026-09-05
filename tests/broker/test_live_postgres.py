# ABOUTME: The broker served by uvicorn against a live Postgres MemoryStore
# ABOUTME: (auto-skipped without DATABASE_URL). Proves criterion 1 end to end:
# ABOUTME: real token, real store, real git resolver, tenant from the claim only.

from __future__ import annotations

import os
import pathlib
import socket
import threading
import time

import httpx
import pytest

from reflect_kb.postgres import InsertMemoryInput, MemoryStore, Tenant

from .conftest import REPO, WS_A, WS_B

pytestmark = pytest.mark.integration

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"


@pytest.fixture
def live_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("REFLECT_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("no DATABASE_URL: live broker test skipped")
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable ({exc})")
    with conn, conn.cursor() as cur:
        for name in sorted(p.name for p in _MIGRATIONS.glob("000*.sql")):
            try:
                cur.execute((_MIGRATIONS / name).read_text())
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"migration {name} did not apply ({exc})")
        cur.execute(
            "truncate reflect_memory.memory_items, reflect_memory.entities, "
            "reflect_memory.edges cascade;"
        )
    return dsn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_broker_serves_pinned_evidence_for_the_token_tenant_only(
    live_dsn, issuer, git_repo, resolver
) -> None:
    import psycopg
    import uvicorn
    from psycopg.rows import dict_row

    from reflect_kb.broker.app import create_app, psycopg_store_factory

    _, sha = git_repo
    with psycopg.connect(live_dsn, row_factory=dict_row) as conn:
        store = MemoryStore(conn)
        a, b = Tenant(workspace_id=WS_A), Tenant(workspace_id=WS_B)
        pinned = store.insert_memory(
            InsertMemoryInput(
                tenant=a,
                content="The auth middleware validates the bearer token on every request",
                source_type="codebase_note",
                source_uri=f"{REPO}@{sha}:src/auth.rs#L2-L5",
                metadata={"classification": "internal"},
            )
        )
        store.insert_memory(
            InsertMemoryInput(
                tenant=a,
                content="The auth middleware once had a token expiry bug (unresolvable pin)",
                source_type="codebase_note",
                source_uri=f"{REPO}@{'0' * 40}:src/auth.rs",
            )
        )
        store.insert_memory(
            InsertMemoryInput(
                tenant=a,
                content="Free-text auth token note with no pin at all",
                source_type="note",
                source_uri="notes/auth.md",
            )
        )
        store.insert_memory(
            InsertMemoryInput(
                tenant=b,
                content="Tenant B secret about the auth token that must never cross over",
                source_type="codebase_note",
                source_uri=f"{REPO}@{sha}:src/auth.rs",
            )
        )

    app = create_app(
        verifier=issuer.verifier(),
        store_factory=psycopg_store_factory(live_dsn),
        resolver=resolver,
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    base = f"http://127.0.0.1:{port}"
    try:
        # Tenant A: one pinned + resolved hit; the other two are refused and counted.
        r = httpx.post(
            f"{base}/v1/evidence",
            json={"query": "auth token", "workspace_id": WS_B},
            headers={"Authorization": f"Bearer {issuer.mint(workspace_id=WS_A)}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workspace_id"] == WS_A
        assert [h["memory_id"] for h in body["lexical"]] == [pinned.id]
        assert body["lexical"][0]["source"] == {
            "repo": REPO,
            "sha": sha,
            "path": "src/auth.rs",
            "line_start": 2,
            "line_end": 5,
        }
        assert body["meta"]["dropped"] == {
            "unpinned": 1,
            "unresolvable": 1,
            "classified": 0,
            "unverified_edges": 0,
        }
        assert "Tenant B secret" not in r.text

        # Tenant B, same query: only B's row, and only because it is pinned.
        r = httpx.post(
            f"{base}/v1/evidence",
            json={"query": "auth token"},
            headers={"Authorization": f"Bearer {issuer.mint(workspace_id=WS_B)}"},
        )
        assert r.status_code == 200
        assert [h["content"][:15] for h in r.json()["lexical"]] == ["Tenant B secret"]
        assert r.json()["workspace_id"] == WS_B

        # No token and no-claim token, over the wire.
        assert httpx.post(f"{base}/v1/evidence", json={"query": "auth"}).status_code == 401
        r = httpx.post(
            f"{base}/v1/evidence",
            json={"query": "auth"},
            headers={"Authorization": f"Bearer {issuer.mint(workspace_id=None)}"},
        )
        assert r.status_code == 403
    finally:
        server.should_exit = True
        thread.join(timeout=10)
