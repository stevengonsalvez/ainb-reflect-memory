"""A note indexed under a shareable label and relabelled restricted: the floor
stops the new write, and the purge removes every ng_* row and graph node or
edge the old label left behind."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("nano_graphrag")

sys.path.insert(0, str(Path(__file__).parent))
from test_cross_machine_graphrag import WS
from test_floor_before_chunking import INTERNAL, _engine

pytestmark = pytest.mark.integration

RESTRICTED_AUTH = INTERNAL.replace("classification: internal", "classification: restricted")


def _rows(dsn) -> dict[str, int]:
    import psycopg

    c = psycopg.connect(dsn, autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute("select namespace, count(*) from reflect_memory.ng_kv where workspace_id=%s group by 1", (WS,))
            out = {f"kv:{ns}": n for ns, n in cur.fetchall()}
            cur.execute("select count(*) from reflect_memory.ng_kv where workspace_id=%s and value::text ilike %s",
                        (WS, "%auth middleware%"))
            out["kv_with_text"] = cur.fetchone()[0]
            cur.execute("select namespace, count(*) from reflect_memory.ng_vectors where workspace_id=%s group by 1", (WS,))
            out.update({f"vec:{ns}": n for ns, n in cur.fetchall()})
            cur.execute("select count(*) from reflect_memory.ng_graph_nodes where workspace_id=%s", (WS,))
            out["nodes"] = cur.fetchone()[0]
            cur.execute("select count(*) from reflect_memory.ng_graph_edges where workspace_id=%s", (WS,))
            out["edges"] = cur.fetchone()[0]
        return out
    finally:
        c.close()


def test_relabel_after_index_purges_every_ng_row(clean, tmp_path) -> None:
    dsn = clean
    eng = _engine(tmp_path / "a", dsn)
    assert eng.insert_documents_batch([(INTERNAL, None, "internal")]) == 1
    before = _rows(dsn)
    assert before.get("kv:full_docs") == 1 and before["kv_with_text"] >= 1 and before["nodes"] >= 1, before

    eng2 = _engine(tmp_path / "b", dsn)
    assert eng2.insert_documents_batch([(RESTRICTED_AUTH, None, "restricted")]) == 0
    assert eng2.purge_local_only([RESTRICTED_AUTH]) == 1
    after = _rows(dsn)
    assert after.get("kv:full_docs", 0) == 0 and after.get("kv:text_chunks", 0) == 0, after
    assert after["kv_with_text"] == 0 and after.get("vec:chunks", 0) == 0, after
    assert after["nodes"] == 0 and after["edges"] == 0 and after.get("vec:entities", 0) == 0, after
    assert after.get("kv:community_reports", 0) == 0
    # Nothing left to purge, and a purge of an unknown note is a no-op.
    assert eng2.purge_local_only([RESTRICTED_AUTH]) == 0


def test_purge_matches_the_stored_note_not_a_note_sharing_its_title(clean, tmp_path) -> None:
    """A title is not an identity: purging a restricted note must not delete a
    different shareable note that carries the same title."""
    dsn = clean
    unrelated = "---\ntitle: auth\nclassification: internal\n---\n\nA different note about session cookies.\n"
    assert _engine(tmp_path / "a", dsn).insert_documents_batch([(unrelated, None, "internal")]) == 1
    assert _engine(tmp_path / "b", dsn).purge_local_only([RESTRICTED_AUTH]) == 0
    assert _rows(dsn).get("kv:full_docs") == 1
    # The same note with only its label changed still matches, also when the
    # key was absent before (the default label).
    unlabelled = INTERNAL.replace("classification: internal\n", "")
    assert _engine(tmp_path / "c", dsn).insert_documents_batch([(unlabelled, None, "internal")]) == 1
    assert _engine(tmp_path / "d", dsn).purge_local_only([RESTRICTED_AUTH]) == 1
    # One note was purged, not two: the unrelated note is only reopened for
    # the next reindex, because both notes fed the same placeholder node
    # (see test_a_reopened_note_is_rebuilt_by_the_next_reindex).
    assert _engine(tmp_path / "e", dsn).insert_documents_batch([(unrelated, None, "internal")]) == 1
    assert _rows(dsn).get("kv:full_docs") == 1
    assert _engine(tmp_path / "f", dsn).purge_local_only([RESTRICTED_AUTH]) == 0


def test_same_note_identity_ignores_only_the_label() -> None:
    from reflect_kb.postgres.nanographrag.purge import _same_note

    assert _same_note(RESTRICTED_AUTH, INTERNAL)
    assert _same_note(RESTRICTED_AUTH, INTERNAL.replace("classification: internal\n", ""))
    assert not _same_note(RESTRICTED_AUTH, INTERNAL.replace("The auth middleware", "The session cookie"))
    assert not _same_note(RESTRICTED_AUTH, INTERNAL.replace("title: auth", "title: auth\ncategory: web"))
    assert not _same_note("---\ntitle: [unclosed\n---\nbody\n", "---\ntitle: other\n---\nbody\n")


def test_mode1_engine_purges_nothing(tmp_path) -> None:
    from reflect_kb.cli import graph_engine as ge

    eng = ge.LearningsGraphEngine.__new__(ge.LearningsGraphEngine)
    eng._pg_dsn = None
    eng._workspace_id = None
    assert eng.purge_local_only([RESTRICTED_AUTH]) == 0


# --------------------------------------------------------------------------- #
# A note sharing an entity with another note, and community reports
# --------------------------------------------------------------------------- #

SECRET = "vault-rotation-secret"
SHARED_NOTE = "---\ntitle: shared\nclassification: internal\n---\n\nThe vault key rotation signs the JWT nightly.\n"
RELABELLED = SHARED_NOTE.replace("classification: internal", "classification: restricted")
OTHER_NOTE = "---\ntitle: other\nclassification: internal\n---\n\nThe auth middleware validates the JWT.\n"
CACHE_NOTE = "---\ntitle: cache\nclassification: internal\n---\n\nRedis cache backs the session store.\n"


def _clique(names: list[str], relation: str) -> str:
    return "##".join(f'("relationship"<|>"{a}"<|>"{b}"<|>"{relation}"<|>2)' for i, a in enumerate(names) for b in names[i + 1:])


# Two communities joined by one bridge: {JWT, VAULT, AUTH MIDDLEWARE}, which
# the relabelled note feeds, and a cache clique that it never touches.
_CACHE = ["REDIS CACHE", "SESSION STORE", "COOKIE JAR", "TTL POLICY"]
_ENTITIES = {
    "vault key rotation": (
        f'("entity"<|>"JWT"<|>"concept"<|>"Signed with the {SECRET} key")##'
        f'("entity"<|>"VAULT"<|>"component"<|>"Holds the {SECRET}")##'
        f'("relationship"<|>"VAULT"<|>"JWT"<|>"the {SECRET} signs the JWT"<|>2)##'
        f'("relationship"<|>"VAULT"<|>"AUTH MIDDLEWARE"<|>"serves the {SECRET}"<|>2)'
    ),
    "auth middleware": (
        '("entity"<|>"JWT"<|>"concept"<|>"A signed token")##'
        '("entity"<|>"AUTH MIDDLEWARE"<|>"component"<|>"Checks tokens")##'
        '("relationship"<|>"AUTH MIDDLEWARE"<|>"JWT"<|>"validates the token"<|>2)'
    ),
    "redis cache": (
        "##".join(f'("entity"<|>"{n}"<|>"component"<|>"part of the cache")' for n in _CACHE)
        + "##" + _clique(_CACHE, "cache link")
        + '##("relationship"<|>"SESSION STORE"<|>"AUTH MIDDLEWARE"<|>"sessions are checked"<|>1)'
    ),
}


async def _echoing_llm(prompt, system_prompt=None, history_messages=None, **kwargs):
    """Extraction per chunk from _ENTITIES; a community report that quotes the
    entity descriptions it was given, the way a real model summarises them."""
    import json

    kwargs.pop("hashing_kv", None)
    low, sys_low = (prompt or "").lower(), (system_prompt or "").lower()
    if "-goal-" in low[:400] and "text document" in low[:400]:
        tail = low[-600:]
        body = next((v for k, v in _ENTITIES.items() if k in tail), "")
        return body + "<|COMPLETE|>"
    if "points" in sys_low and "score" in sys_low:
        return json.dumps({"points": [{"description": "from a surviving report", "score": 10}]})
    if "community" in low or "report" in low or "community" in sys_low:
        quoted = SECRET if SECRET in (prompt or "") else "no secret here"
        return json.dumps({"title": "c", "summary": quoted, "findings": [], "rating": 5.0, "rating_explanation": "x"})
    return "No additional information available."


def _echo_graph(working_dir, dsn):
    from nano_graphrag import GraphRAG
    from test_cross_machine_graphrag import _fake_embedding, _install_graspologic_shim

    from reflect_kb.postgres.nanographrag import addon_params, storage_classes

    _install_graspologic_shim()
    return GraphRAG(
        working_dir=str(working_dir), embedding_func=_fake_embedding(), best_model_func=_echoing_llm,
        cheap_model_func=_echoing_llm, enable_naive_rag=True, **storage_classes(),
        addon_params=addon_params(pg_dsn=dsn, workspace_id=WS, embedding_model="test-fake"),
    )


def _secret_rows(dsn) -> dict[str, list]:
    import psycopg

    like = f"%{SECRET}%"
    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("select namespace, key from reflect_memory.ng_kv where workspace_id=%s and value::text ilike %s", (WS, like))
        kv = cur.fetchall()
        cur.execute("select node_id from reflect_memory.ng_graph_nodes where workspace_id=%s and attrs::text ilike %s", (WS, like))
        nodes = cur.fetchall()
        cur.execute("select source, target from reflect_memory.ng_graph_edges where workspace_id=%s and attrs::text ilike %s",
                    (WS, like))
        edges = cur.fetchall()
    return {"kv": kv, "nodes": nodes, "edges": edges}


def _entity_vector_ids(dsn) -> set[str]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("select id from reflect_memory.ng_vectors where workspace_id=%s and namespace='entities'", (WS,))
        return {r[0] for r in cur.fetchall()}


def _node_ids(dsn) -> set[str]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("select node_id from reflect_memory.ng_graph_nodes where workspace_id=%s", (WS,))
        return {r[0] for r in cur.fetchall()}


def test_relabel_removes_restricted_text_from_shared_nodes_edges_vectors_and_reports(clean, tmp_path) -> None:
    from nano_graphrag import QueryParam
    from nano_graphrag._utils import compute_mdhash_id

    from reflect_kb.postgres.nanographrag.purge import purge_notes

    dsn = clean
    _echo_graph(tmp_path / "a", dsn).insert([SHARED_NOTE, OTHER_NOTE, CACHE_NOTE])
    before = _secret_rows(dsn)
    assert before["nodes"] and before["edges"], before  # JWT's merged description carries the secret
    assert any(ns == "community_reports" for ns, _ in before["kv"]), before
    assert _rows(dsn).get("kv:community_reports", 0) >= 2  # the cache community has its own report
    def ent(name: str) -> str:
        return compute_mdhash_id(name, prefix="ent-")

    assert {ent('"JWT"'), ent('"VAULT"')} <= _entity_vector_ids(dsn)

    assert purge_notes(dsn, WS, [RELABELLED]) == 1

    assert _secret_rows(dsn) == {"kv": [], "nodes": [], "edges": []}
    assert _rows(dsn).get("kv:community_reports", 0) >= 1, "a report that never covered the note was deleted"
    nodes = _node_ids(dsn)
    assert '"JWT"' not in nodes and '"VAULT"' not in nodes, nodes
    assert {f'"{n}"' for n in _CACHE} | {'"AUTH MIDDLEWARE"'} <= nodes, nodes
    vectors = _entity_vector_ids(dsn)
    assert ent('"JWT"') not in vectors and ent('"VAULT"') not in vectors, vectors  # embedded over name+description
    assert {ent(n) for n in nodes} <= vectors

    # Global search still answers from the reports that never covered the note,
    # and local search does not trip over a node whose community was removed.
    reader = _echo_graph(tmp_path / "b", dsn)
    assert "from a surviving report" in reader.query("redis session", QueryParam(mode="global", only_need_context=True))
    assert "REDIS CACHE" in reader.query("redis cache", QueryParam(mode="local", only_need_context=True)).upper()
    reader.query("auth middleware", QueryParam(mode="local", only_need_context=True))


def _stored(dsn, namespace: str) -> set[str]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
        cur.execute("select key from reflect_memory.ng_kv where workspace_id=%s and namespace=%s", (WS, namespace))
        return {r[0] for r in cur.fetchall()}


def test_a_reopened_note_is_rebuilt_by_the_next_reindex(clean, tmp_path) -> None:
    """The purge takes a node the relabelled note shared with a note that
    stays shareable. nano-graphrag inserts neither a document nor a chunk it
    already stored, so that node comes back only because the purge reopened
    the surviving note's chunks. The note that fed no removed node keeps
    its chunks: reopening is per chunk, not per corpus."""
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    dsn = clean
    _echo_graph(tmp_path / "a", dsn).insert([SHARED_NOTE, OTHER_NOTE, CACHE_NOTE])
    indexed_chunks = _stored(dsn, "text_chunks")  # one per note, they are short
    assert len(_stored(dsn, "full_docs")) == 3 and len(indexed_chunks) == 3

    assert purge_notes(dsn, WS, [RELABELLED]) == 1
    # The relabelled note is gone, the note that fed the shared JWT node is
    # reopened, the cache note is untouched.
    assert len(_stored(dsn, "full_docs")) == 1, "only the cache note is still stored"
    kept = _stored(dsn, "text_chunks")
    assert len(kept) == 1 and kept < indexed_chunks, kept
    assert '"JWT"' not in _node_ids(dsn)

    # What reindex does next: insert every note that is still shareable.
    _echo_graph(tmp_path / "b", dsn).insert([OTHER_NOTE, CACHE_NOTE])
    nodes = _node_ids(dsn)
    assert {'"JWT"', '"AUTH MIDDLEWARE"'} <= nodes, nodes
    assert '"VAULT"' not in nodes, nodes  # only the relabelled note described it
    assert _secret_rows(dsn) == {"kv": [], "nodes": [], "edges": []}
    rebuilt = _stored(dsn, "text_chunks")
    assert len(_stored(dsn, "full_docs")) == 2 and kept < rebuilt < indexed_chunks, rebuilt


def test_purge_refuses_a_remote_connection_without_tls(monkeypatch) -> None:
    """The purge sends notes' doc ids and reads their text over the DSN, so it
    takes the same TLS judgement as every other shared-store connection."""
    from types import SimpleNamespace

    from reflect_kb.postgres.dsn import ALLOW_INSECURE_VAR, InsecureDSNError
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    monkeypatch.delenv(ALLOW_INSECURE_VAR, raising=False)
    seen: dict = {}

    class Plaintext:
        info = SimpleNamespace(host="db.example.net", hostaddr="", ssl_in_use=False)
        closed = False

        def close(self) -> None:
            self.closed = True

    conn = Plaintext()

    def connect(dsn, **kwargs):
        seen.update(kwargs)
        return conn

    with pytest.raises(InsecureDSNError):
        purge_notes("postgresql://u@db.example.net/reflect", WS, [RESTRICTED_AUTH], connect=connect)
    assert conn.closed and seen.get("sslmode") == "require"
