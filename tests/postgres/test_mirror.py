"""reflect add and reindex mirror a note into the shared store (Mode 2)."""

from __future__ import annotations

import pytest

from reflect_kb.cli.entity_store import DocumentEntities, Entity, Relationship
from reflect_kb.postgres.mirror import MirrorError, mirror_note

WS = "11111111-1111-1111-1111-111111111111"
SHA = "3f2a9c1d4e5b6a7f8091a2b3c4d5e6f708192a3b"
NOTE = "---\ntitle: JWT expiry\ncategory: auth\nkey_insight: k\nrepo: acme/widgets\ncommit: " + SHA + "\nsource_path: src/auth.rs\n---\n\nThe auth middleware validates the JWT.\n"
FM = {"title": "JWT expiry", "category": "auth", "key_insight": "k", "repo": "acme/widgets", "commit": SHA, "source_path": "src/auth.rs"}


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        self._last = sql

    def fetchone(self):
        import uuid

        rid = str(uuid.uuid4())
        if "memory_items" in self._last:
            return {"id": rid, "workspace_id": WS, "agent_id": None, "source_session_id": None, "user_id": None,
                    "source_type": "learning_note", "source_uri": f"acme/widgets@{SHA}:src/auth.rs", "content": "x",
                    "content_hash": "h", "metadata": {"classification": "internal"}, "confidence": 0.5,
                    "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        if "entities" in self._last:
            return {"id": rid, "workspace_id": WS, "canonical_name": "n", "entity_type": "t", "aliases": [],
                    "metadata": {}, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        return {"id": rid, "workspace_id": WS, "source_entity_id": "a", "target_entity_id": "b", "relation_type": "r",
                "evidence_memory_id": None, "weight": 1.0, "metadata": {}, "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z"}


class _Conn:
    def __init__(self, log):
        self.log, self.autocommit = log, False

    def cursor(self):
        return _Cursor(self.log)

    def commit(self):
        self.log.append(("commit", None))

    def close(self):
        self.log.append(("close", None))


def test_mirror_writes_note_entities_and_edges_bound_to_the_workspace() -> None:
    log: list = []
    ents = DocumentEntities(document_id="d", entities=[Entity("Auth Middleware", "component", "validates"),
                                                        Entity("JWT", "concept", "token")],
                            relationships=[Relationship("Auth Middleware", "JWT", "validates", "", 8)])
    res = mirror_note("postgresql://w@localhost/db", WS, content=NOTE, frontmatter=FM, doc_entities=ents,
                      connect=lambda dsn: _Conn(log))
    assert res.memory_id and res.entities == 2 and res.edges == 1 and res.skipped is None
    binds = [p for sql, p in log if "set_config('app.current_workspace'" in str(sql)]
    assert binds and all(p == (WS,) for p in binds), "every call binds the workspace"
    inserts = [sql for sql, _ in log if isinstance(sql, str) and sql.lstrip().lower().startswith("insert")]
    assert len(inserts) == 4  # memory item, two entities, one edge
    assert ("close", None) in log


def test_mirror_keeps_restricted_notes_local_and_reports_connection_failures() -> None:
    res = mirror_note("postgresql://w@localhost/db", WS, content=NOTE,
                      frontmatter={**FM, "classification": "restricted"}, connect=lambda dsn: _Conn([]))
    assert res.memory_id is None and "never leaves the local store" in res.skipped

    def boom(dsn):
        raise OSError("connection refused")

    with pytest.raises(MirrorError, match="could not connect"):
        mirror_note("postgresql://w@localhost/db", WS, content=NOTE, frontmatter=FM, connect=boom)


def test_a_plain_remote_connection_is_a_mirror_error_not_a_crash() -> None:
    """Item 34: an insecure DSN used to escape as InsecureDSNError past the
    CLI's except MirrorError, aborting reflect add after the local write."""
    from types import SimpleNamespace

    import pytest

    from reflect_kb.postgres.mirror import MirrorError, mirror_note

    class _RemoteConn:
        info = SimpleNamespace(host="db.example.com", hostaddr="", ssl_in_use=False)

        def close(self):
            pass

    with pytest.raises(MirrorError, match="could not connect"):
        mirror_note("postgresql://u@db.example.com/x", "0cccccc0-0000-4000-8000-00000000cccc",
                    content="---\ntitle: t\n---\nbody", frontmatter={"title": "t"},
                    connect=lambda d: _RemoteConn())


def test_mirror_redacts_a_legacy_note_before_any_row_is_written() -> None:
    """Review round four, item 1: reindex hands mirror_note the raw content
    and sidecar of a note written before the capture gate. No credential may
    reach a memory_items, entities or edges payload, whichever caller it is."""
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    log: list = []
    note = NOTE.replace("The auth middleware", f"export GH={token}; the auth middleware")
    ents = DocumentEntities(document_id="d", entities=[Entity(f"token {token}", "credential", f"was {token}"),
                                                        Entity("JWT", "concept", "token")],
                            relationships=[Relationship(f"token {token}", "JWT", f"rotates {token}", "", 8)])
    res = mirror_note("postgresql://w@localhost/db", WS, content=note, frontmatter={**FM, "title": f"rotate {token}"},
                      doc_entities=ents, connect=lambda dsn: _Conn(log))
    assert res.entities == 2 and res.edges == 1, res
    payload = repr(log)
    assert token not in payload
    assert "<REDACTED:github_token>" in payload


# --------------------------------------------------------------------------- #
# Relabelled after mirroring (live database)
# --------------------------------------------------------------------------- #

RESTRICTED_NOTE = NOTE.replace("---\ntitle:", "---\nclassification: restricted\ntitle:")


def _pinned_hits(dsn: str, query: str) -> list[str]:
    """What the broker's pinned search would serve for ``query``."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select set_config('app.current_workspace', %s, false)", (WS,))
        cur.execute("select content from reflect_memory.search_pinned_memory(%s, %s, 10)", (WS, query))
        return [row[0] for row in cur.fetchall()]


def _mirror_counts(dsn: str) -> dict[str, int]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        out = {}
        for table in ("memory_items", "entities", "edges"):
            cur.execute(f"select count(*) from reflect_memory.{table} where workspace_id=%s", (WS,))
            out[table] = cur.fetchone()[0]
        return out


@pytest.mark.integration
def test_relabelling_a_mirrored_note_stops_the_pinned_search_serving_it(clean) -> None:
    """Review round five, merge blocker: the purge covered only the ng_*
    tables, so a note mirrored while internal and relabelled restricted kept
    its memory_items row, pinned and shareable, and the broker went on
    serving its whole content. reindex skips the note before mirroring, so
    nothing rewrote that row."""
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    dsn = clean
    ents = DocumentEntities(
        document_id="d",
        entities=[Entity("Auth Middleware", "component", "validates the JWT"), Entity("JWT", "concept", "a token")],
        relationships=[Relationship("Auth Middleware", "JWT", "validates", "", 8)],
    )
    res = mirror_note(dsn, WS, content=NOTE, frontmatter=FM, doc_entities=ents)
    assert res.memory_id and res.entities == 2 and res.edges == 1, res
    assert any("auth middleware" in hit.lower() for hit in _pinned_hits(dsn, "auth middleware JWT"))
    assert _mirror_counts(dsn) == {"memory_items": 1, "entities": 2, "edges": 1}

    assert purge_notes(dsn, WS, [RESTRICTED_NOTE]) == 1

    assert _pinned_hits(dsn, "auth middleware JWT") == []
    assert _mirror_counts(dsn) == {"memory_items": 0, "entities": 0, "edges": 0}
    # Mirroring the note again is refused by the floor, and a second purge
    # has nothing left to take.
    assert mirror_note(dsn, WS, content=RESTRICTED_NOTE,
                       frontmatter={**FM, "classification": "restricted"}).skipped
    assert purge_notes(dsn, WS, [RESTRICTED_NOTE]) == 0
    assert _mirror_counts(dsn) == {"memory_items": 0, "entities": 0, "edges": 0}


@pytest.mark.integration
def test_the_purge_matches_the_redacted_row_of_a_legacy_note(clean) -> None:
    """A note written before the capture gate still carries its secret on
    disk, while the mirror stored the redacted text. The purge is handed the
    file, so it matches both forms or that row survives the relabel."""
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    dsn = clean
    legacy = NOTE.replace("The auth middleware", f"export GH={token}; the auth middleware")
    assert mirror_note(dsn, WS, content=legacy, frontmatter=FM).memory_id
    assert _mirror_counts(dsn)["memory_items"] == 1

    relabelled = legacy.replace("---\ntitle:", "---\nclassification: restricted\ntitle:")
    assert purge_notes(dsn, WS, [relabelled]) == 1
    assert _mirror_counts(dsn)["memory_items"] == 0


@pytest.mark.integration
def test_an_entity_the_note_named_is_purged_even_with_no_relationship(clean) -> None:
    """Round five review: entities carry no provenance, and the sidecar's
    auto-generated descriptions are boilerplate ("Technology referenced in
    document"), so an entity the note contributed without a relationship has
    nothing tying it to the memory item. Its canonical name is the note's own
    words, and that is what the purge matches; the label on the row still
    says internal, so the broker would go on serving the name."""
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    dsn = clean
    ents = DocumentEntities(
        document_id="d",
        entities=[Entity("auth middleware", "component", "Technology referenced in document")],
        relationships=[],
    )
    assert mirror_note(dsn, WS, content=NOTE, frontmatter=FM, doc_entities=ents).entities == 1
    assert _mirror_counts(dsn) == {"memory_items": 1, "entities": 1, "edges": 0}

    assert purge_notes(dsn, WS, [RESTRICTED_NOTE]) == 1
    assert _mirror_counts(dsn) == {"memory_items": 0, "entities": 0, "edges": 0}


@pytest.mark.integration
def test_a_note_relabelled_and_rewritten_is_matched_by_its_pin(clean) -> None:
    """Content identity ignores only the classification key, so a relabel that
    also edits the body is a different note by that rule and used to keep its
    row: full pre-edit content, pinned, shareable. The pin is the same source
    at the same commit, and that is what the broker serves on."""
    from reflect_kb.postgres.nanographrag.purge import purge_notes

    dsn = clean
    assert mirror_note(dsn, WS, content=NOTE, frontmatter=FM).memory_id
    rewritten = RESTRICTED_NOTE.replace("The auth middleware validates the JWT.",
                                        "The auth middleware validates the JWT on every request.")

    assert purge_notes(dsn, WS, [rewritten]) == 0, "the note itself was not found by content"
    assert _pinned_hits(dsn, "auth middleware JWT") == []
    assert _mirror_counts(dsn)["memory_items"] == 0
