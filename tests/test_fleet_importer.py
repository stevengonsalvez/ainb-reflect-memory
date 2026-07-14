"""Tests for ``reflect fleet ingest`` — the fleet-lambda importer.

Every test runs against a throwaway KB (``GLOBAL_LEARNINGS_PATH``) and state dir
(``REFLECT_STATE_DIR``); the promotion-metric test also redirects
``metrics.METRICS_PATH`` so nothing touches the real ``~/.learnings``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from reflect_kb import metrics
from reflect_kb.cli.learnings_cli import cli
from reflect_kb.fleet import importer as importer_mod

FIXTURES = Path(__file__).parent / "fixtures" / "fleet"


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    """Point the KB, state dir, and metrics sink at tmp locations."""
    kb = tmp_path / "learnings"
    state = tmp_path / "state"
    (kb / "documents").mkdir(parents=True)
    state.mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(state))
    monkeypatch.setattr(metrics, "METRICS_PATH", state / "metrics.jsonl")
    return {"kb": kb, "state": state, "metrics": state / "metrics.jsonl"}


def _docs(kb: Path) -> list[dict]:
    out = []
    for md in sorted((kb / "documents").glob("*.md")):
        text = md.read_text()
        fm, body = _parse(text)
        fm["_path"] = md
        fm["_body"] = body
        out.append(fm)
    return out


def _parse(text: str) -> tuple[dict, str]:
    parts = text.split("---", 2)
    return yaml.safe_load(parts[1]) or {}, parts[2].strip()


def test_import_creates_files_with_full_frontmatter(kb_env):
    result = importer_mod.ingest(FIXTURES, ["patterns", "discoveries", "corrections"])

    assert result.imported > 0
    assert result.errors == 0

    docs = _docs(kb_env["kb"])
    assert len(docs) == result.imported

    # Every doc carries a stable identity (recall reads `name`; without it the
    # Learning id degrades to "?"), and it equals the content-addressed filename.
    for d in docs:
        assert d["name"] == d["_path"].stem

    disc = next(d for d in docs if d["category"] == "fleet-discovery" and d["workflow_state"] == "open")
    assert disc["source_system"] == "fleet"
    assert disc["source_kind"] == "discoveries"
    assert disc["authority"] == "advisory"
    assert disc["quarantine"] is True
    assert disc["occurrences"] == 1
    assert disc["domain"] in {"coding", "research", "ops", "security", "personal", "writing"}
    assert len(disc["content_hash"]) == 64
    assert "key_insight" in disc and disc["key_insight"]
    assert isinstance(disc["tags"], list)
    # discoveries body carries the problem/solution structure
    assert "## Problem" in disc["_body"] or "## Solution" in disc["_body"]

    # every category is represented
    cats = {d["category"] for d in docs}
    assert {"fleet-pattern", "fleet-discovery", "fleet-correction"} <= cats


def test_reimport_is_idempotent(kb_env):
    first = importer_mod.ingest(FIXTURES, ["patterns", "discoveries"])
    files_after_first = sorted((kb_env["kb"] / "documents").glob("*.md"))

    second = importer_mod.ingest(FIXTURES, ["patterns", "discoveries"])
    files_after_second = sorted((kb_env["kb"] / "documents").glob("*.md"))

    assert second.imported == 0
    assert second.deduped == first.imported
    assert files_after_first == files_after_second  # no new files

    # occurrences bumped to 2 on every re-imported doc
    for d in _docs(kb_env["kb"]):
        if d["source_system"] == "fleet":
            assert d["occurrences"] == 2


def test_third_occurrence_emits_promotion_metric(kb_env, tmp_path):
    root = tmp_path / "single"
    root.mkdir()
    (root / "patterns.jsonl").write_text(
        json.dumps({"title": "Single repeated pattern", "description": "Body text."}) + "\n"
    )

    importer_mod.ingest(root, ["patterns"])
    importer_mod.ingest(root, ["patterns"])
    assert not kb_env["metrics"].exists() or "fleet_promotion_candidate" not in kb_env["metrics"].read_text()

    importer_mod.ingest(root, ["patterns"])  # third occurrence crosses the threshold

    lines = [
        json.loads(l)
        for l in kb_env["metrics"].read_text().splitlines()
        if l.strip()
    ]
    promos = [r for r in lines if r["op"] == "fleet_promotion_candidate"]
    assert len(promos) == 1
    assert promos[0]["count"] == 3
    assert len(promos[0]["hash"]) == 64


def test_malformed_line_skipped_and_reported(kb_env):
    result = importer_mod.ingest(FIXTURES, ["patterns"])
    assert result.skipped >= 1
    assert any("malformed JSON" in d for d in result.skipped_details)


def test_archive_entries_get_archived_state(kb_env):
    importer_mod.ingest(FIXTURES, ["discoveries"])
    archived = [d for d in _docs(kb_env["kb"]) if d["workflow_state"] == "archived"]
    assert archived
    assert all(d["source_kind"] == "discoveries" for d in archived)


def test_retracted_discovery_skipped(kb_env):
    result = importer_mod.ingest(FIXTURES, ["discoveries"])
    bodies = " ".join(d["_body"] for d in _docs(kb_env["kb"]))
    assert "Retracted finding kept for provenance" not in bodies
    assert any("retracted" in d for d in result.skipped_details)


def test_dry_run_writes_nothing(kb_env):
    result = importer_mod.ingest(FIXTURES, ["patterns", "discoveries"], dry_run=True)
    assert result.imported > 0
    assert not list((kb_env["kb"] / "documents").glob("*.md"))
    assert not (kb_env["state"] / "fleet-ledger.json").exists()


def test_cli_dry_run_exits_zero(kb_env):
    result = CliRunner().invoke(
        cli,
        ["fleet", "ingest", "--root", str(FIXTURES), "--dry-run", "--no-reindex"],
    )
    assert result.exit_code == 0, result.output


def test_cli_fleet_group_registered():
    result = CliRunner().invoke(cli, ["fleet", "--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "status" in result.output


def test_status_reports_ledger(kb_env):
    importer_mod.ingest(FIXTURES, ["patterns"])
    result = CliRunner().invoke(cli, ["fleet", "status"])
    assert result.exit_code == 0
    assert "documents" in result.output
