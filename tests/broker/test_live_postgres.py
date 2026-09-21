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
        # A migration that does not apply is a failure of this PR, never a skip.
        for name in sorted(p.name for p in _MIGRATIONS.glob("000*.sql")):
            cur.execute((_MIGRATIONS / name).read_text())
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
        # The unpinned row never leaves SQL (0007 filters before the limit),
        # so only the unresolvable pin is a drop the broker can count.
        assert body["meta"]["dropped"] == {"total": 1}
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


def test_live_path_drops_a_restricted_row_that_predates_the_constraint(live_dsn, issuer, git_repo, resolver) -> None:
    """get_evidence_pack must hand the broker the row's metadata; with an
    empty dict every hit passed the floor. A restricted row can only exist
    from before 0003, so the constraint is lifted to plant one."""
    import psycopg
    from fastapi.testclient import TestClient
    from psycopg.rows import dict_row

    from reflect_kb.broker.app import create_app, psycopg_store_factory

    _, sha = git_repo
    with psycopg.connect(live_dsn, row_factory=dict_row, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("alter table reflect_memory.memory_items drop constraint if exists memory_items_classification_floor")
        cur.execute(
            "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash, source_uri, metadata) "
            "values (%s, 'codebase_note', 'restricted auth token note', 'hr', %s, '{\"classification\":\"restricted\"}'::jsonb)",
            (WS_A, f"{REPO}@{sha}:src/auth.rs"))
        cur.execute(
            "insert into reflect_memory.memory_items (workspace_id, source_type, content, content_hash, source_uri, metadata) "
            "values (%s, 'codebase_note', 'internal auth token note', 'hi', %s, '{\"classification\":\"internal\"}'::jsonb)",
            (WS_A, f"{REPO}@{sha}:src/auth.rs"))
    try:
        app = create_app(verifier=issuer.verifier(), store_factory=psycopg_store_factory(live_dsn), resolver=resolver)
        r = TestClient(app).post("/v1/evidence", json={"query": "auth token"},
                                 headers={"Authorization": f"Bearer {issuer.mint(workspace_id=WS_A)}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "restricted" not in r.text
        assert [h["content"] for h in body["lexical"]] == ["internal auth token note"]
        # The read function filters the restricted row in SQL (0003), so the
        # broker's own floor sees only what survives; that floor now reads the
        # real metadata the store hands it, proven here on the live path.
        from reflect_kb.postgres import EvidencePackQuery, MemoryStore, Tenant

        with psycopg.connect(live_dsn, row_factory=dict_row) as conn:
            pack = MemoryStore(conn).get_evidence_pack(EvidencePackQuery(tenant=Tenant(workspace_id=WS_A), query="auth token"))
        assert [h.metadata.get("classification") for h in pack.lexical] == ["internal"]
    finally:
        # The shared test database is reused: remove the planted row and put
        # the constraint back so the next migration pass does not stop on it.
        with psycopg.connect(live_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("delete from reflect_memory.memory_items where content_hash in ('hr', 'hi')")
            cur.execute("alter table reflect_memory.memory_items add constraint memory_items_classification_floor "
                        "check (metadata->>'classification' is null or metadata->>'classification' in ('public', 'internal'))")



def _uri_corpus(sha: str) -> list[str]:
    repos = ["acme/widgets", "widgets", "a.b/c-d_e", "acme//widgets", "acme widgets", "ACME/w"]
    shas = [sha, sha[:7], sha[:6], sha.upper(), "g" * 40, "0" * 64, "0" * 65]
    spaces = ["\u00a0", "\u1680", "\u180e", "\u2028", "\u3000", "\u0085", "\x1c", "\x1f", "\u200b", "\ufeff"]
    paths = [f"a{sp}b" for sp in spaces] + ["src/auth.rs", "a", "./a", "a/./b", "a/../b", "../a", "/abs", "a//b", "a/", "a b", "a\tb", "é/ü.rs", ".hidden", "..x"]
    suffixes = spaces + ["", "#L3", "#L3-L9", "#L3-9", "#L9-L3", "#L0", "#L3-L", "#x", "\n", "#L3\n", "#L٣", " ", "\n\n"]
    corpus = [f"{r}@{s}:{p}{x}" for r in repos for s in shas for p in paths for x in suffixes]
    # Every BMP codepoint (no NUL, no surrogates) in the path, at its end, and
    # after the sha: the SQL side must not depend on the server's locale.
    sweep = [chr(c) for c in range(1, 0x10000) if not 0xD800 <= c <= 0xDFFF]
    corpus += [f"acme/widgets@{sha}:a{ch}b" for ch in sweep]
    corpus += [f"acme/widgets@{sha}:src/a{ch}" for ch in sweep]
    corpus += [f"acme/widgets@{sha}{ch}:a" for ch in sweep]
    return corpus + ["", "src/auth.rs", "notes/auth.md", f"acme/widgets@{sha}", f"acme/widgets@{sha}:", f"@{sha}:a"]


def test_sql_pin_predicate_never_rejects_a_pin_the_python_parser_accepts(live_dsn, git_repo) -> None:
    """0007 filters unpinned rows in SQL before LIMIT; a row the broker's
    parser would serve must never be filtered there. SQL may accept a little
    more (the broker re-parses): malformed line ranges and whitespace in the
    path, which SQL does not test so as not to depend on the server locale."""
    import psycopg

    from reflect_kb.pinning import SourcePinError, parse_source_uri

    _, sha = git_repo
    corpus = _uri_corpus(sha)
    with psycopg.connect(live_dsn) as conn, conn.cursor() as cur:
        cur.execute("select u, reflect_memory.is_pinned_source_uri(u) from unnest(%s::text[]) u", (corpus,))
        sql_ok = dict(cur.fetchall())
        cur.execute("select reflect_memory.is_pinned_source_uri(null)")
        assert cur.fetchone()[0] is False

    def py_ok(uri: str) -> bool:
        try:
            parse_source_uri(uri)
            return True
        except SourcePinError:
            return False

    accepted = [u for u in corpus if py_ok(u)]
    assert accepted, "corpus must contain valid pins"
    assert [u for u in accepted if not sql_ok[u]] == []
    looser = [u for u in corpus if sql_ok[u] and not py_ok(u)]
    assert all("#" in u or any(ch.isspace() for ch in u) for u in looser), looser[:5]


def test_pinned_search_finds_pinned_rows_below_a_page_of_unpinned_ones(live_dsn, git_repo) -> None:
    """A workspace whose top p_limit matches are unpinned: search_memory's
    page has no pinned row for the broker to keep, search_pinned_memory
    filters before LIMIT and returns the pinned match. Run as the role that
    will call it, reflect_broker under FORCE RLS, and across tenants."""
    import psycopg
    from psycopg.rows import dict_row

    _, sha = git_repo
    with psycopg.connect(live_dsn, row_factory=dict_row) as conn:
        store = MemoryStore(conn)
        a = Tenant(workspace_id=WS_A)
        pinned = store.insert_memory(InsertMemoryInput(
            tenant=a, content="kestrel rotation note", source_type="codebase_note",
            source_uri=f"{REPO}@{sha}:src/auth.rs"))
        for i in range(8):
            store.insert_memory(InsertMemoryInput(
                tenant=a, content=f"kestrel kestrel kestrel rotation rotation unpinned {i}",
                source_type="note", source_uri=None if i % 2 else f"notes/kestrel-{i}.md"))
        conn.commit()

        def as_broker(bound: str, workspace: str, fn: str) -> list[dict]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("set local role reflect_broker")
                cur.execute("select set_config('app.current_workspace', %s, true)", (bound,))
                cur.execute(f"select id, source_uri from reflect_memory.{fn}(%s, 'kestrel rotation', 5)", (workspace,))
                return cur.fetchall()

        assert all(r["source_uri"] != pinned.source_uri for r in as_broker(WS_A, WS_A, "search_memory"))
        assert [str(r["id"]) for r in as_broker(WS_A, WS_A, "search_pinned_memory")] == [pinned.id]
        assert as_broker(WS_B, WS_B, "search_pinned_memory") == []
        assert as_broker(WS_B, WS_A, "search_pinned_memory") == []  # RLS: bound to B, asking for A
