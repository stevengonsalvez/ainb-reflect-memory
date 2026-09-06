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
