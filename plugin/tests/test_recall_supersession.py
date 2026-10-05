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
import sqlite3
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


def _seed_retired_noise(kb, n: int) -> None:
    """n retired notes unrelated to any candidate: n archived/ files, n
    .forgotten/ files and n retired ledger rows each pointing at a note file."""
    archived = kb.root / "archived"
    forgotten = kb.docs / ".forgotten"
    noise = kb.root / "noise"
    for d in (archived, forgotten, noise):
        d.mkdir(exist_ok=True)
    rows = []
    for i in range(n):
        stem = f"unrelated-topic-{i}-{i % 4096 + 0x10000:x}"[:60]
        body = _note(f"lrn-unrelated-topic-{i}-{i:06x}", f"Unrelated {i}", body="kubernetes ingress")
        (archived / f"{stem}.md").write_text(body)
        (forgotten / f"{stem}.md").write_text(body)
        (noise / f"{stem}.md").write_text(body)
        rows.append((f"lrn-ledger-noise-{i}", str(noise / f"{stem}.md"), f"noisehash{i}"))
    conn = reflect_db.init_db(kb.db)
    try:
        with conn:
            tid = reflect_db.add_learning("template", conn=conn)
            conn.row_factory = sqlite3.Row
            tpl = dict(conn.execute("SELECT * FROM learnings WHERE id = ?", (tid,)).fetchone())
            conn.row_factory = None
            conn.execute(
                "UPDATE learnings SET is_latest = 0, status = 'superseded' WHERE id = ?", (tid,)
            )
            cols = list(tpl)
            conn.executemany(
                f"INSERT INTO learnings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [
                    tuple(
                        {**tpl, "id": lid, "artifact_path": path, "content_hash": h,
                         "is_latest": 0, "status": "superseded"}[c]
                        for c in cols
                    )
                    for lid, path, h in rows
                ],
            )
    finally:
        reflect_db.close_all()


def test_unrelated_retired_notes_are_not_read(kb, monkeypatch):
    """Cost must not scale with the retired backlog: 2000 retired notes in the
    ledger, archived/ and .forgotten/ that match no candidate are never opened,
    while a retired note that does match a candidate is still dropped."""
    n = 2000
    _seed_retired_noise(kb, n)
    (kb.root / "archived" / "redis-pool-exhaustion-old-advice-aaaaaa.md").write_text(OLD)

    monkeypatch.setenv("REFLECT_DB_PATH", str(kb.db))
    reads: list[Path] = []
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **kw):
        reads.append(self)
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    lo = recall_mod.Learning(chunk_text="old", frontmatter=recall_mod.parse_frontmatter(OLD)[0])
    ln = recall_mod.Learning(chunk_text="new", frontmatter=recall_mod.parse_frontmatter(NEW)[0])
    kept = recall_mod.filter_superseded([lo, ln], kb.docs)

    assert [x.id for x in kept] == ["lrn-pool-new-bbbbbb"]
    assert len(reads) <= 2, f"read {len(reads)} retired notes for 2 candidates"


def test_archived_note_named_like_live_id_does_not_drop_live_note(kb):
    """Matching is on id, not name: an archived note whose *name* equals a live
    note's id (and whose file name links to it, so it is opened) must not drop it."""
    archived = kb.root / "archived"
    archived.mkdir()
    (archived / "something-else-bbbbbb.md").write_text(
        "---\nid: lrn-something-else-bbbbbb\nname: lrn-pool-new-bbbbbb\n"
        "title: Other\nconfidence: high\n---\nother note\n"
    )
    kb.serve(OLD, NEW)
    assert sorted(kb.run("--no-cache")) == ["lrn-pool-new-bbbbbb", "lrn-pool-old-aaaaaa"]


def test_dotted_stem_is_not_mistaken_for_a_collision_suffix(kb):
    """python-3.12 is a name, not python-3 plus a collision counter."""
    forgotten = kb.docs / ".forgotten"
    forgotten.mkdir()
    (forgotten / "python-3.12.md").write_text(
        _note("lrn-python-312-dddddd", "Python 3.12 retired advice")
    )
    py3 = _note("python-3", "Python 3 pool advice")
    kb.serve(py3, NEW)
    assert sorted(kb.run("--no-cache")) == ["lrn-pool-new-bbbbbb", "python-3"]


def test_sweep_collision_suffix_maps_to_base_stem_only_when_base_exists(tmp_path):
    """The sweep writes <stem>.<i>.md only when <stem>.md is already there."""
    docs = tmp_path / "kb" / "documents"
    forgotten = docs / ".forgotten"
    forgotten.mkdir(parents=True)
    for name in ("dup-note.md", "dup-note.1.md", "dup-note.2.md", "lone.1.md", "python-3.12.md"):
        (forgotten / name).write_text("---\nid: x\n---\nbody\n")
    keys = recall_mod._archived_dir_keys(docs, set(), set())
    assert "dup-note" in keys
    assert "lone" not in keys and "python-3" not in keys
    assert {"dup-note.1", "lone.1", "python-3.12"} <= keys


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
