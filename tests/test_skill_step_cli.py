"""`reflect skill-step`: one command surface for the skill's scripted steps."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from reflect_kb.cli import learnings_cli
from reflect_kb.cli import skill_step_cli

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"

VALID_SIDECAR = """document_id: lrn-abc123
extracted_at: "2026-08-11T10:00:00Z"
entities:
  - name: reflect
    type: tool
    description: the reflect CLI that indexes learning notes
relationships: []
"""
NOTE = """---
title: JWT expiry off by one
category: debugging-sessions
confidence: high
key_insight: use <= in the expiry check
tags: [jwt]
---

## Problem
Tokens expired one second early.

## Solution
Use <= in the expiry check.
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLECT_SKILL_SCRIPTS_DIR", str(SCRIPTS))
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(tmp_path / "kb"))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_NO_DAEMON", "1")
    monkeypatch.delenv("REFLECT_DRAIN_RECEIPT", raising=False)
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: (_ for _ in ()).throw(RuntimeError("no engine")))
    return tmp_path


def test_scripts_dir_is_found_from_the_explicit_variable_first(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REFLECT_SKILL_SCRIPTS_DIR", str(tmp_path))
    assert skill_step_cli.scripts_dir() != tmp_path  # no validate_sidecar.py there: not that one
    monkeypatch.setenv("REFLECT_SKILL_SCRIPTS_DIR", str(SCRIPTS))
    assert skill_step_cli.scripts_dir() == SCRIPTS


@pytest.mark.parametrize("step,args,expect", [
    ("state", ["status"], 0),
    ("validate-sidecar", ["--strict", "{sidecar}"], 0),
    ("validate-sidecar", ["--strict", "{bad}"], 1),
    ("metrics", ["--help"], 0),
])
def test_steps_delegate_to_the_skill_scripts(env, step, args, expect) -> None:
    sidecar = env / "n.entities.yaml"
    sidecar.write_text(VALID_SIDECAR)
    bad = env / "bad.entities.yaml"
    bad.write_text("entities:\n  - name: x\n")
    args = [a.format(sidecar=sidecar, bad=bad) for a in args]
    result = CliRunner().invoke(learnings_cli.cli, ["skill-step", step, *args])
    assert result.exit_code == expect, result.output


def test_index_validates_then_adds_and_writes_the_receipt(env, monkeypatch) -> None:
    note = env / "n.md"
    note.write_text(NOTE)
    sidecar = env / "n.entities.yaml"
    sidecar.write_text(VALID_SIDECAR)
    receipt = env / "receipt.jsonl"
    monkeypatch.setenv("REFLECT_DRAIN_RECEIPT", str(receipt))
    result = CliRunner().invoke(learnings_cli.cli, ["skill-step", "index", str(note), str(sidecar)])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "Document id: jwt-expiry-off-by-one-" in flat
    rows = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["note"] == str(note.resolve())
    assert rows[0]["doc_id"].startswith("jwt-expiry-off-by-one-")
    assert (env / "kb" / "documents" / f"{rows[0]['doc_id']}.md").is_file()


def test_index_refuses_a_malformed_sidecar_without_indexing(env, monkeypatch) -> None:
    note = env / "n.md"
    note.write_text(NOTE)
    sidecar = env / "n.entities.yaml"
    sidecar.write_text("entities:\n  - name: x\n")
    receipt = env / "receipt.jsonl"
    monkeypatch.setenv("REFLECT_DRAIN_RECEIPT", str(receipt))
    result = CliRunner().invoke(learnings_cli.cli, ["skill-step", "index", str(note), str(sidecar)])
    assert result.exit_code != 0 and "sidecar validation failed" in result.output
    assert not receipt.exists() and not list((env / "kb" / "documents").glob("*.md"))


def test_index_fails_loudly_when_the_add_fails(env, monkeypatch) -> None:
    note = env / "n.md"
    note.write_text("no frontmatter at all\n")
    sidecar = env / "n.entities.yaml"
    sidecar.write_text(VALID_SIDECAR)
    result = CliRunner().invoke(learnings_cli.cli, ["skill-step", "index", str(note), str(sidecar)])
    assert result.exit_code != 0, result.output


def test_missing_scripts_dir_is_a_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REFLECT_SKILL_SCRIPTS_DIR", str(tmp_path / "nowhere"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(skill_step_cli, "_candidates", lambda: iter([tmp_path / "nowhere"]))
    result = CliRunner().invoke(learnings_cli.cli, ["skill-step", "state", "status"])
    assert result.exit_code != 0 and "REFLECT_SKILL_SCRIPTS_DIR" in result.output
