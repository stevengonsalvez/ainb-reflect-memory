# ABOUTME: Behavioural proof that recall never injects a superseded or archived note.
# ABOUTME: Drives recall.py end to end against a fake `reflect` CLI, a temp KB and a temp ledger.
"""Supersession is enforced at recall time, not only recorded in the ledger.

A query matches an old note and the note that replaced it. Recall must inject
only the replacement. Retirement signals covered:

  * frontmatter ``superseded_by`` / ``status: superseded|archived``
  * the KB's ``archived/`` dir (serve soft-archive) while a stale graph cache
    still returns the note
  * the SQLite ledger (``is_latest = 0``), matched through ``artifact_path``
  * no ledger at all: nothing is dropped, nothing crashes

``REFLECT_RECALL_INCLUDE_SUPERSEDED=1`` is the debugging opt-out.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
RECALL = PLUGIN_ROOT / "skills" / "recall" / "scripts" / "recall.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(RECALL.parent))

import reflect_db  # noqa: E402
import recall as recall_mod  # noqa: E402

QUERY = "redis connection pool exhaustion"


def _note(
    note_id: str, title: str, *, extra_fm: str = "", body: str = "redis connection pool exhaustion fix"
) -> str:
    return (
        f"---\nid: {note_id}\ntitle: {title}\nconfidence: high\n{extra_fm}---\n"
        f"{title}: {body}\n"
    )


OLD = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice")
NEW = _note("lrn-pool-new-bbbbbb", "Redis pool exhaustion current advice")


@pytest.fixture()
def kb(tmp_path):
    """A throwaway KB + HOME + fake `reflect` CLI that returns whatever notes
    the test lists in ``served.json`` (so a retired note can still be served,
    exactly like a stale graph cache would)."""
    home = tmp_path / "home"
    kb_root = tmp_path / "kb"
    (kb_root / "documents").mkdir(parents=True)
    home.mkdir()
    served = tmp_path / "served.json"
    script = tmp_path / "bin" / "reflect"
    script.parent.mkdir()
    script.write_text(f"""#!/usr/bin/env python3
import json
notes = json.load(open({str(served)!r}))
print(json.dumps({{"context": "--New Chunk--".join(notes)}}))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    class KB:
        root = kb_root
        docs = kb_root / "documents"
        bin_dir = script.parent
        db = tmp_path / "ledger" / "reflect.db"

        @staticmethod
        def serve(*notes: str) -> None:
            served.write_text(json.dumps(list(notes)))

        @staticmethod
        def run(*args: str, env_extra: dict[str, str] | None = None) -> list[str]:
            env = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{script.parent}:/usr/bin:/bin",
                "GLOBAL_LEARNINGS_PATH": str(kb_root),
                "REFLECT_STATE_DIR": str(tmp_path / "state"),
                "REFLECT_DB_PATH": str(KB.db),
                "RECALL_CROSS_ENCODER": "0",
                "RECALL_MMR": "0",
                "RECALL_GRAPH_ARM": "0",
                "RECALL_GAP_LOG": "0",
                "RECALL_FOLLOWUP": "0",
            }
            env.pop("REFLECT_RECALL_INCLUDE_SUPERSEDED", None)
            env.update(env_extra or {})
            proc = subprocess.run(
                [sys.executable, str(RECALL), QUERY, "--format", "json", *args],
                capture_output=True, text=True, timeout=60, env=env,
            )
            assert proc.returncode == 0, proc.stderr
            return [r["id"] for r in json.loads(proc.stdout)["results"]]

    return KB


def test_frontmatter_superseded_by_drops_old_note(kb):
    old = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
                extra_fm="superseded_by: lrn-pool-new-bbbbbb\n")
    kb.serve(old, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]


def test_opt_out_returns_both(kb):
    old = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
                extra_fm="superseded_by: lrn-pool-new-bbbbbb\n")
    kb.serve(old, NEW)
    ids = kb.run("--no-cache", env_extra={"REFLECT_RECALL_INCLUDE_SUPERSEDED": "1"})
    assert sorted(ids) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


@pytest.mark.parametrize("status", ["superseded", "archived"])
def test_frontmatter_status_retired_is_dropped(kb, status):
    old = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
                extra_fm=f"status: {status}\n")
    kb.serve(old, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]


def test_live_status_and_null_superseded_by_are_kept(kb):
    live = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
                 extra_fm="status: indexed\nsuperseded_by: null\n")
    kb.serve(live, NEW)
    assert sorted(kb.run("--no-cache")) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


def test_archived_dir_note_excluded_despite_stale_graph_cache(kb):
    """serve soft-archive moves the file to <kb>/archived/ but the graph cache
    keeps returning the note until a reindex. Recall must not inject it."""
    archived = kb.root / "archived"
    archived.mkdir()
    # file name (slug-hash6) differs from the frontmatter id (lrn- prefix)
    (archived / "redis-pool-exhaustion-old-advice-aaaaaa.md").write_text(OLD)
    (kb.docs / "redis-pool-exhaustion-current-advice-bbbbbb.md").write_text(NEW)
    kb.serve(OLD, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]
    ids = kb.run("--no-cache", env_extra={"REFLECT_RECALL_INCLUDE_SUPERSEDED": "1"})
    assert "lrn-pool-old-aaaaaa" in ids


def test_forgotten_dir_note_excluded(kb):
    forgotten = kb.docs / ".forgotten"
    forgotten.mkdir()
    (forgotten / "redis-pool-exhaustion-old-advice-aaaaaa.1.md").write_text(OLD)
    kb.serve(OLD, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]


def test_ledger_is_latest_false_drops_old_note(kb):
    """Ledger-only retirement (contradiction demotion): the note file carries no
    supersession marker, the ledger row points at it via artifact_path."""
    note_path = kb.docs / "redis-pool-exhaustion-old-advice-aaaaaa.md"
    note_path.write_text(OLD)
    conn = reflect_db.init_db(kb.db)
    try:
        old_id = reflect_db.add_learning(
            "Redis pool exhaustion old advice", artifact_path=str(note_path), conn=conn,
        )
        new_id = reflect_db.add_learning("Redis pool exhaustion current advice", conn=conn)
        with conn:
            conn.execute(
                "UPDATE learnings SET is_latest = 0, superseded_by_learning_id = ? WHERE id = ?",
                (new_id, old_id),
            )
    finally:
        reflect_db.close_all()
    kb.serve(OLD, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]
    ids = kb.run("--no-cache", env_extra={"REFLECT_RECALL_INCLUDE_SUPERSEDED": "1"})
    assert sorted(ids) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


def test_ledger_status_archived_drops_note_matched_by_content_hash(kb):
    old = _note(
        "lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
        extra_fm="provenance:\n  content_hash: deadbeef01\n",
    )
    conn = reflect_db.init_db(kb.db)
    try:
        lid = reflect_db.add_learning(
            "Redis pool exhaustion old advice", content_hash="deadbeef01", conn=conn,
        )
        with conn:
            conn.execute("UPDATE learnings SET status = 'archived' WHERE id = ?", (lid,))
    finally:
        reflect_db.close_all()
    kb.serve(old, NEW)
    assert kb.run("--no-cache") == ["lrn-pool-new-bbbbbb"]


def test_live_ledger_row_keeps_note(kb):
    note_path = kb.docs / "redis-pool-exhaustion-old-advice-aaaaaa.md"
    note_path.write_text(OLD)
    conn = reflect_db.init_db(kb.db)
    try:
        reflect_db.add_learning(
            "Redis pool exhaustion old advice", artifact_path=str(note_path), conn=conn,
        )
    finally:
        reflect_db.close_all()
    kb.serve(OLD, NEW)
    assert sorted(kb.run("--no-cache")) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


def test_no_ledger_no_archive_dirs_is_graceful(kb):
    assert not kb.db.exists()
    kb.serve(OLD, NEW)
    assert sorted(kb.run("--no-cache")) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]
    assert not kb.db.exists(), "recall must never create the ledger"


def test_cached_fetch_is_filtered_and_opt_out_still_sees_raw(kb):
    """The cache stores the raw fetch, so the filter must run on a cache hit too."""
    old = _note("lrn-pool-old-aaaaaa", "Redis pool exhaustion old advice",
                extra_fm="superseded_by: lrn-pool-new-bbbbbb\n")
    kb.serve(old, NEW)
    first = kb.run()  # populates the cache
    assert first == ["lrn-pool-new-bbbbbb"]
    kb.serve()  # backend now returns nothing: only the cache can answer
    assert kb.run() == ["lrn-pool-new-bbbbbb"]
    ids = kb.run(env_extra={"REFLECT_RECALL_INCLUDE_SUPERSEDED": "1"})
    assert sorted(ids) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


def test_filter_superseded_unit_empty_and_opt_out(monkeypatch):
    assert recall_mod.filter_superseded([]) == []
    lrn = recall_mod.Learning(
        chunk_text="x", frontmatter={"id": "a", "superseded_by": "b"},
    )
    monkeypatch.setenv("REFLECT_RECALL_INCLUDE_SUPERSEDED", "1")
    assert recall_mod.filter_superseded([lrn]) == [lrn]
    monkeypatch.setenv("REFLECT_RECALL_INCLUDE_SUPERSEDED", "0")
    assert recall_mod.filter_superseded([lrn]) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
