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

    import reflect_config
    import reflect_db

    # reflect_config caches the resolved config, so reload it first or the
    # env override is ignored and the rows land in the operator's real
    # ~/.reflect/reflect.db.
    importlib.reload(reflect_config)
    importlib.reload(reflect_db)
    assert reflect_db.db_path() == tmp_path / "reflect.db", reflect_db.db_path()
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


def test_export_redacts_a_pre_gate_note_in_the_bundle(tmp_path) -> None:
    """Item 9: documents/*.md are bundled as text; a note written before the
    capture gate existed must not leave the machine with its secret."""
    import io
    import tarfile

    import kb_export

    home = tmp_path / "learnings"
    docs = home / "documents"
    docs.mkdir(parents=True)
    (docs / "leaky-note.md").write_text(
        f"---\ntitle: rotate the token\ncategory: ops\n---\n\nexport GH={FAKE_TOKEN}\n", encoding="utf-8")
    (docs / "leaky-note.entities.yaml").write_text(f"entities:\n  - name: {FAKE_TOKEN}\n    type: tool\n", encoding="utf-8")
    kb_export.export_kb(tmp_path / "kb.tar", db_path=tmp_path / "missing.db", learnings_home=home)
    with tarfile.open(tmp_path / "kb.tar") as tar:
        names = tar.getnames()
        assert "documents/leaky-note.md" in names and "documents/leaky-note.entities.yaml" in names
        for name in ("documents/leaky-note.md", "documents/leaky-note.entities.yaml"):
            payload = tar.extractfile(name).read().decode("utf-8")
            assert FAKE_TOKEN not in payload and "<REDACTED:github_token>" in payload, name


def test_export_leaves_ids_paths_and_timestamps_alone(tmp_path, monkeypatch) -> None:
    """Item 14: only free-text columns pass the redactor; an id or a path that
    merely resembles a credential is copied as is."""
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    import importlib

    import kb_export
    import reflect_config
    import reflect_db

    importlib.reload(reflect_config)
    importlib.reload(reflect_db)
    conn = reflect_db.get_conn()
    weird_path = "/tmp/AKIAIOSFODNN7EXAMPLE/report.md"
    conn.execute("insert into learnings (id, title, category, confidence, status, source_path, created_at) "
                 "values (?, ?, 'ops', 'LOW', 'pending', ?, '2026-01-01T00:00:00Z')",
                 ("lrn-fixed-id", f"note with {FAKE_TOKEN}", weird_path))
    conn.commit()
    rows = kb_export._ordered_rows(conn, "learnings", ["id", "title", "source_path"])
    assert rows == [("lrn-fixed-id", "note with <REDACTED:github_token>", weird_path)]


def test_skill_summary_is_redacted() -> None:
    import skill_index

    assert FAKE_TOKEN not in skill_index._summarize(f"uses {FAKE_TOKEN} for the registry")


def test_every_free_text_writer_redacts_at_the_database_boundary(tmp_path, monkeypatch) -> None:
    """Review round four, item 9: only add_learning redacted. Observations,
    persona values, proposal diffs, slot content and recall queries were
    stored raw. Nothing in any table (events included) may carry the token."""
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    import importlib

    import reflect_config
    import reflect_db

    importlib.reload(reflect_config)
    importlib.reload(reflect_db)
    assert reflect_db.db_path() == tmp_path / "reflect.db", reflect_db.db_path()
    conn = reflect_db.get_conn()
    leak = f"export GH={FAKE_TOKEN}"

    lid = reflect_db.add_learning(title="rotate nightly", category="ops", source_quote="q", conn=conn)
    oid = reflect_db.add_observation(f"team rotates tokens: {leak}", conn=conn)
    assert reflect_db.add_observation_evidence(oid, ["c1"], content=f"team rotates tokens nightly: {leak}", conn=conn)
    reflect_db.upsert_persona_field("proj", "deploy", f"uses {leak}", conn=conn)
    reflect_db.upsert_persona_field("proj", "deploy", f"still uses {leak}", source_observation_ids=[oid], conn=conn)
    reflect_db.add_proposal(lid, "agents/ops.md", f"+ {leak}\n", conn=conn)
    reflect_db.ensure_default_slots("proj", conn=conn)
    assert reflect_db.slot_replace("guidance", f"hazard: {leak}", project_id="proj", conn=conn)["ok"]
    assert reflect_db.slot_append("guidance", f"again {leak}", project_id="proj", conn=conn)["ok"]
    assert reflect_db.slot_auto_append("project_context", [f"auto {leak}"], project_id="proj", conn=conn)
    assert reflect_db.slot_auto_replace("user_preferences", f"prefs {leak}", conn=conn)
    first = reflect_db.record_recall_search(f"why does {leak} fail", [lid], session_id="s1", conn=conn)
    assert first["recall_event_ids"]
    # The same query again is a retry, not a followup, although the stored text is redacted.
    lid2 = reflect_db.add_learning(title="other", category="ops", source_quote="q2", conn=conn)
    again = reflect_db.record_recall_search(f"why does {leak} fail", [lid2], session_id="s1", conn=conn)
    assert again["followup"] is False
    conn.commit()

    dump = "\n".join(conn.iterdump())
    assert FAKE_TOKEN not in dump
    for table, column in (("observations", "content"), ("project_persona", "value"), ("proposals", "diff"),
                          ("slots", "content"), ("recall_events", "query")):
        cells = [r[0] for r in conn.execute(f"select {column} from {table}").fetchall()]
        assert any("<REDACTED:github_token>" in (c or "") for c in cells), table


def test_auto_append_redacts_a_pem_block_split_across_lines(tmp_path, monkeypatch) -> None:
    """Review round five: the hook writer redacted line by line, so a PEM
    block lost only its BEGIN line and the key body was stored in the clear."""
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    import importlib

    import reflect_config
    import reflect_db

    importlib.reload(reflect_config)
    importlib.reload(reflect_db)
    conn = reflect_db.get_conn()
    reflect_db.ensure_default_slots("proj", conn=conn)
    body = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU"
    pem = ["-----BEGIN OPENSSH PRIVATE KEY-----", body, "-----END OPENSSH PRIVATE KEY-----"]
    assert reflect_db.slot_auto_append("project_context", pem, project_id="proj", conn=conn)
    conn.commit()
    stored = conn.execute("select content from slots where name = 'project_context'").fetchone()[0]
    assert body not in stored and "END OPENSSH PRIVATE KEY" not in stored, stored
    assert "<REDACTED:private_key>" in stored
    assert body not in "\n".join(conn.iterdump())
