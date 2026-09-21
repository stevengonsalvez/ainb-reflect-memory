"""Where the CLI calls the purge, and in what order.

Review round five, minor item: the note stored under its pre-redaction id was
unlinked, but the chunks, vectors and reports that id produced were left
behind, so the secret stayed searchable until someone forced a reindex. The
unlink now purges that copy from the shared store, and says what to run when
the index is a per-machine Mode 1 store no purge reaches.

The reindex order is pinned here too: the purge also removes rows a note that
stays shared with the purged one, so it has to run before the mirror and the
batch that write those rows back.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml
from click.testing import CliRunner

from reflect_kb.cli import learnings_cli

GITHUB_TOKEN = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
WS = "11111111-1111-1111-1111-111111111111"


def _note_with_a_secret() -> str:
    fm = {"title": "Deploy failed until the token was rotated", "category": "debugging-sessions",
          "key_insight": "Rotate the deploy token"}
    return f"---\n{yaml.safe_dump(fm, sort_keys=True)}---\n\nThe script exported GITHUB_TOKEN={GITHUB_TOKEN}.\n"


class _Engine:
    """The graph engine as `reflect add` uses it, Mode 2 unless told otherwise."""

    def __init__(self, shared: bool = True) -> None:
        self.shared_store_target = ("postgresql://u@localhost/db", WS) if shared else None
        self.purged: list[list[str]] = []

    def insert_document(self, text, entities_formatted=None, label=None):
        return SimpleNamespace(indexed=True, reason=None)

    def purge_local_only(self, notes):
        self.purged.append(list(notes))
        return 1 if self.shared_store_target else 0


def _kb_with_the_unredacted_copy(tmp_path: Path, monkeypatch) -> tuple[Path, Path, str]:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    monkeypatch.setattr(learnings_cli, "_mirror_to_shared_store", lambda *a, **k: None)
    raw = _note_with_a_secret()
    frontmatter, raw_body = learnings_cli.parse_frontmatter(raw)
    old_id = learnings_cli.generate_document_id(frontmatter["title"], raw_body)
    (kb / learnings_cli.DOCUMENTS_DIR / f"{old_id}.md").write_text(raw, encoding="utf-8")
    src = tmp_path / "note.md"
    src.write_text(raw, encoding="utf-8")
    return kb, src, raw


def test_the_unredacted_copy_is_purged_from_the_shared_store(tmp_path: Path, monkeypatch) -> None:
    _, src, raw = _kb_with_the_unredacted_copy(tmp_path, monkeypatch)
    engine = _Engine()
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)

    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])

    assert result.exit_code == 0, result.output
    # The purge is asked for the note as it was indexed: unredacted, under the
    # old id. The redacted note that add writes is indexed, never purged.
    assert engine.purged == [[raw]], engine.purged
    assert "Purged the unredacted copy from the shared store" in " ".join(result.output.split())


def test_a_mode_1_index_is_told_what_to_rebuild(tmp_path: Path, monkeypatch) -> None:
    """No shared store means no purge: per-machine files keep the copy until a
    forced reindex, so the instruction is printed instead of swallowed."""
    _, src, _ = _kb_with_the_unredacted_copy(tmp_path, monkeypatch)
    engine = _Engine(shared=False)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)

    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])

    assert result.exit_code == 0, result.output
    assert engine.purged == []
    assert "learnings reindex --force" in " ".join(result.output.split())


def test_a_clean_note_purges_nothing(tmp_path: Path, monkeypatch) -> None:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    monkeypatch.setattr(learnings_cli, "_mirror_to_shared_store", lambda *a, **k: None)
    engine = _Engine()
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)
    src = tmp_path / "note.md"
    src.write_text("---\ntitle: clean\ncategory: c\nkey_insight: k\n---\n\nNothing secret here.\n", encoding="utf-8")

    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])

    assert result.exit_code == 0, result.output
    assert engine.purged == []


# --------------------------------------------------------------------------- #
# reindex: the purge runs before the mirror rewrites the notes that stay
# --------------------------------------------------------------------------- #


class _ReindexEngine:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.shared_store_target = ("postgresql://u@localhost/db", WS)

    def local_only(self, text, label=None):
        return label in {"restricted", "pii"}

    def purge_local_only(self, notes):
        self.order.append("purge")
        return len(notes)

    def insert_documents_batch(self, batch):
        self.order.append("index")
        return len(batch)


def test_reindex_purges_before_it_mirrors_the_notes_that_stay(tmp_path: Path, monkeypatch) -> None:
    """The purge takes rows a surviving note shared with the relabelled one
    (an entity it described, a graph node it fed). Mirroring first would hand
    those rows straight back to the purge, so a reindex would leave the
    broker short of every shared entity until the one after it."""
    kb = tmp_path / "kb"
    docs = kb / learnings_cli.DOCUMENTS_DIR
    docs.mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    (docs / "keep.md").write_text(
        "---\ntitle: keep\ncategory: c\nkey_insight: k\nclassification: internal\n---\n\nThe JWT is signed.\n",
        encoding="utf-8")
    (docs / "relabelled.md").write_text(
        "---\ntitle: relabelled\ncategory: c\nkey_insight: k\nclassification: restricted\n---\n\nThe vault key.\n",
        encoding="utf-8")
    order: list[str] = []
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: _ReindexEngine(order))
    monkeypatch.setattr(learnings_cli, "_mirror_to_shared_store", lambda *a, **k: order.append("mirror"))

    result = CliRunner().invoke(learnings_cli.cli, ["reindex"])

    assert result.exit_code == 0, result.output
    assert order == ["purge", "mirror", "index"], order
