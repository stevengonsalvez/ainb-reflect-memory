"""reflect add and the classification floor: a malformed label is refused
with the intended message, and a floor skip is reported, not called indexed."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner


class _Engine:
    """Stands in for the graph engine: reports the floor's decision."""

    def __init__(self, blocked: str | None = None) -> None:
        self.blocked = blocked
        self.purged: list[list[str]] = []

    def insert_document(self, text, entities_formatted=None, label=None):
        from reflect_kb.cli.graph_engine import InsertStatus

        if self.blocked:
            return InsertStatus(False, f"classification {self.blocked} stays in the local store")
        return InsertStatus(True)

    def purge_local_only(self, notes):
        self.purged.append(list(notes))
        return 1 if self.blocked else 0


def _note(label: str) -> str:
    return f"---\ntitle: floor probe\ncategory: tooling\nkey_insight: probe\nclassification: {label}\n---\n\nbody\n"


def _run(tmp_path: Path, monkeypatch, text: str, engine: _Engine):
    from reflect_kb.cli import learnings_cli as lc

    kb = tmp_path / "kb"
    (kb / "documents").mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(lc, "_get_graph_engine", lambda: engine)
    note = tmp_path / "note.md"
    note.write_text(text, encoding="utf-8")
    result = CliRunner().invoke(lc.cli, ["add", str(note), "--force"])
    return result, (result.output or "") + (getattr(result, "stderr", "") or "")


def test_a_list_label_is_refused_with_the_intended_message(tmp_path, monkeypatch) -> None:
    result, out = _run(tmp_path, monkeypatch, _note("[public]"), _Engine())
    assert result.exit_code == 2, out
    assert "classification must be one of" in out and "['public']" in out


def test_a_mapping_label_is_refused_too(tmp_path, monkeypatch) -> None:
    result, out = _run(tmp_path, monkeypatch, _note("{level: public}"), _Engine())
    assert result.exit_code == 2, out
    assert "classification must be one of" in out


def test_a_floor_skip_is_reported_and_the_old_copy_purged(tmp_path, monkeypatch) -> None:
    engine = _Engine(blocked="restricted")
    result, out = _run(tmp_path, monkeypatch, _note("restricted"), engine)
    assert result.exit_code == 0, out
    flat = " ".join(out.split())  # the console wraps long lines at 80 columns
    assert "Skipped by the classification floor: classification restricted stays in the local store" in flat, out
    assert "Indexed into graph" not in flat
    assert "Purged 1 earlier copy" in flat and engine.purged and "restricted" in engine.purged[0][0]


def test_an_indexed_note_still_says_so(tmp_path, monkeypatch) -> None:
    result, out = _run(tmp_path, monkeypatch, _note("internal"), _Engine())
    assert result.exit_code == 0, out
    assert "Indexed into graph" in out and "Skipped" not in out
