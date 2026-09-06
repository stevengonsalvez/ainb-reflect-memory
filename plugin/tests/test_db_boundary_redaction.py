"""A belief-revision CREATE carrying a credential leaves no credential in
reflect.db, in the export, or in a skill summary (item 40)."""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN / "scripts"))

FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"


def test_add_learning_redacts_at_the_database_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    import importlib

    import reflect_db

    importlib.reload(reflect_db)
    conn = reflect_db.get_conn()
    lid = reflect_db.add_learning(title=f"rotate {FAKE_TOKEN} nightly", category="ops",
                                  source_quote=f"export GH={FAKE_TOKEN}", conn=conn)
    row = conn.execute("select title, source_quote from learnings where id = ?", (lid,)).fetchone()
    assert FAKE_TOKEN not in row[0] and FAKE_TOKEN not in row[1]
    assert "<REDACTED:github_token>" in row[0]
    conn.commit()
    # Defence in depth: the export re-redacts every text cell, so a row that
    # predates the boundary cannot leave the machine either.
    conn.execute("insert into learnings (id, title, category, confidence, status, created_at) values (?, ?, 'ops', 'LOW', 'pending', '2026-01-01T00:00:00Z')",
                 ("legacy", f"old row with {FAKE_TOKEN}"))
    conn.commit()
    import kb_export

    rows = kb_export._ordered_rows(conn, "learnings", ["id", "title"])
    assert all(FAKE_TOKEN not in str(c) for r in rows for c in r)
    snapshot = kb_export.build_db_snapshot(tmp_path / "reflect.db")
    assert FAKE_TOKEN.encode() not in snapshot


def test_skill_summary_is_redacted() -> None:
    import skill_index

    assert FAKE_TOKEN not in skill_index._summarize(f"uses {FAKE_TOKEN} for the registry")
