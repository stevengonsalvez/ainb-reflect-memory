"""Behavioural tests for the OKF v0.2 storage profile.

Every note writer reflect has must leave an OKF-conformant note on disk (SPEC
§11: parseable frontmatter with a non-empty `type`, plus well-formed trust and
provenance families) while keeping every reflect key as an extension key. The
Rule S9 carve-out may rewrite ONLY `status` / `verified` / `stale_after`.

All writes go to tmp dirs; nothing touches ~/.learnings or a real qmd index.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from reflect_kb import metrics, okf, okf_profile, write_flow
from reflect_kb.cli import learnings_cli
from reflect_kb.fleet import importer as fleet_importer
from reflect_kb.serve import KnowledgeBase

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin"
HOOK = PLUGIN / "skills" / "recall" / "hooks" / "user_prompt_submit_recall.py"

LEGACY_NOTE = """\
---
# reflect comment that must survive
type: LEARNING
id: lrn-legacy-abc123
created: 2026-04-24
title: "cost --- benefit"
confidence: high
confidence_num: 0.9
key_insight: "Hooks must always exit 0. Otherwise the harness surfaces errors."
tags:
- hooks
- reliability
causal_relations:
  - source: hook
    target: crash
    type: causes
forget_after: 2026-12-31T00:00:00Z
provenance:
  source_tool: claude
  source_path: /tmp/session.jsonl

  content_hash: abc
---

## Problem

A body line with --- dashes stays byte-identical.
"""


def _assert_conformant(text: str) -> dict:
    """Conformant (SPEC §11), clean against the §5 family rules reflect's own
    writers follow, AND read identically by the production parser."""
    fm, _ = okf.parse_note(text)
    assert okf.okf_problems(fm) == [], fm
    assert okf.okf_warnings(fm) == [], fm
    assert learnings_cli.parse_frontmatter(text)[0] == fm
    return fm


def _body(text: str) -> str:
    return okf.parse_note(text)[1]


# ---------------------------------------------------------------------------
# The shared core
# ---------------------------------------------------------------------------


def test_vendored_plugin_copy_is_byte_identical():
    src = (REPO / "src" / "reflect_kb" / "okf_profile.py").read_bytes()
    vendored = (PLUGIN / "scripts" / "okf_profile.py").read_bytes()
    assert vendored == src, "re-copy src/reflect_kb/okf_profile.py to plugin/scripts/"


@pytest.mark.parametrize("value", [
    "plain", "yes", "No", "null", "~", "true", "1.0", "0x1F", "2026-01-01",
    "2026-01-01T00:00:00Z", "a: b", "# not a comment", "a, b", "[x]", "{y}",
    "- dash", "human:alice", "/abs/path.md", 'say "hi" \\ there', "tab\there",
    "multi\nline", "unicode é中", "emoji \U0001F600", "sep  \x85",
    "ctl\x7f\x9f﻿", "", " padded ", "---", "line ---", "@at", "`tick`",
    None, True, False, 0, -7, 0.5, 1e-9, -2.5e+300, float("nan"), float("inf"),
    ["a", "yes", 1], {"by": "reflect-cli/1.0", "at": "2026-10-05T00:00:00Z"},
    [{"id": "s", "resource": "/x, y"}], [], {},
])
def test_yaml_value_round_trips_through_pyyaml(value):
    loaded = yaml.safe_load(f"k: {okf.yaml_value(value)}")["k"]
    if isinstance(value, float) and not math.isfinite(value):
        assert loaded == str(value)
    else:
        assert loaded == value


def test_conformance_is_spec_11_only():
    assert okf.is_okf_conformant({"type": "learning"})
    assert not okf.is_okf_conformant({"title": "no type"})
    assert not okf.is_okf_conformant({"type": "  "})
    assert not okf.is_okf_conformant("not a mapping")


@pytest.mark.parametrize("fm", [
    {"type": "x", "sources": [{"id": "no-resource"}]},
    {"type": "x", "generated": {"at": "2026-01-01T00:00:00Z"}},
    {"type": "x", "generated": {"by": "a/1", "at": "2026-01-01T00:00:00"}},
    {"type": "x", "stale_after": "2026-01-01"},
    {"type": "observation", "status": "active"},  # reflect's own vocabulary
])
def test_family_deviations_warn_but_stay_conformant(fm):
    # SPEC §11: consumers MUST NOT reject on optional-family issues.
    assert okf.is_okf_conformant(fm)
    assert okf.okf_warnings(fm)


def test_bare_verified_mapping_is_a_one_element_list():
    fm = {"type": "x", "verified": {"by": "human:a", "at": "2026-01-01T00:00:00+02:00"}}
    assert okf.okf_warnings(fm) == []


# ---------------------------------------------------------------------------
# normalize_note
# ---------------------------------------------------------------------------


def test_normalize_legacy_note_is_conformant_and_keeps_reflect_keys():
    out = okf.normalize_note(LEGACY_NOTE, actor="reflect-cli/9.9")
    fm = _assert_conformant(out)
    before, _ = okf.parse_note(LEGACY_NOTE)

    assert fm["type"] == "learning"  # LEARNING normalized
    for key, value in before.items():
        if key != "type":
            assert fm[key] == value, key  # every reflect key unchanged
    assert fm["description"] == "Hooks must always exit 0. Otherwise the harness surfaces errors."
    assert fm["generated"] == {"by": "reflect-cli/9.9", "at": "2026-04-24T00:00:00Z"}
    assert fm["sources"] == [{"id": "source", "resource": "/tmp/session.jsonl"}]
    assert fm["stale_after"] == "2026-12-31T00:00:00Z"
    assert fm["forget_after"] == before["forget_after"]  # kept for back-compat

    assert _body(out) == _body(LEGACY_NOTE)
    assert "# reflect comment that must survive" in out
    # Untouched frontmatter lines survive verbatim, in order.
    untouched = [ln for ln in LEGACY_NOTE.splitlines() if not ln.startswith("type:")]
    assert [ln for ln in out.splitlines() if ln in untouched] == untouched


def test_normalize_is_idempotent_and_identity_on_conformant_notes():
    once = okf.normalize_note(LEGACY_NOTE, actor="a/1")
    assert okf.normalize_note(once, actor="b/2") == once


def test_normalize_note_without_frontmatter_gets_one():
    out = okf.normalize_note("# Retry with jitter\n\nBack off. Always.\n",
                             actor="a/1", now="2026-10-05T12:00:00Z")
    fm = _assert_conformant(out)
    assert fm["title"] == "Retry with jitter"
    assert fm["description"] == "Back off."
    assert out.endswith("# Retry with jitter\n\nBack off. Always.\n")


@pytest.mark.parametrize("text", [
    "---\ntype: LEARNING\ntitle: t\ntype: LEARNING\n---\nbody\n",  # duplicate key
    "---\ntitle: t\nno closing fence\n",                              # unterminated
    "\ufeff---\ntitle: t\n---\nbody\n",                              # BOM before fence
    "---\n[a, b]\n---\nbody\n",                                       # not a mapping
])
def test_normalize_refuses_ambiguous_frontmatter(text):
    with pytest.raises(ValueError):
        okf.normalize_note(text, actor="a/1")


def test_lifecycle_rewrite_replaces_a_quoted_key_instead_of_duplicating():
    note = '---\ntype: learning\n"status": draft\n---\nbody\n'
    out = okf.set_lifecycle_fields(note, status="deprecated")
    assert out == "---\ntype: learning\nstatus: deprecated\n---\nbody\n"


# ---------------------------------------------------------------------------
# Rule S9 carve-out
# ---------------------------------------------------------------------------


def _frontmatter_lines(text: str) -> list[str]:
    lines = text.split("\n")
    end = lines.index("---", 1)
    return lines[1:end]


def test_lifecycle_rewrite_touches_only_carve_out_keys():
    note = okf.normalize_note(LEGACY_NOTE, actor="a/1")
    out = okf.set_lifecycle_fields(
        note, status="deprecated",
        verified={"by": "human:stevie", "at": "2026-10-05T10:00:00+01:00"},
        stale_after="2027-01-01T00:00:00Z",
    )
    fm = _assert_conformant(out)
    assert fm["status"] == "deprecated"
    assert fm["verified"] == [{"by": "human:stevie", "at": "2026-10-05T10:00:00+01:00"}]
    assert fm["stale_after"] == "2027-01-01T00:00:00Z"

    lifecycle = tuple(f"{k}:" for k in okf.LIFECYCLE_KEYS)
    keep = [ln for ln in _frontmatter_lines(note) if not ln.startswith(lifecycle)]
    assert [ln for ln in _frontmatter_lines(out) if not ln.startswith(lifecycle)] == keep
    assert _body(out) == _body(note)

    # Removing what was added restores the note (stale_after was pre-existing).
    reverted = okf.set_lifecycle_fields(
        out, status=None, verified=None, stale_after="2026-12-31T00:00:00Z")
    assert okf.parse_note(reverted)[0] == okf.parse_note(note)[0]
    assert _body(reverted) == _body(note)


def test_lifecycle_rewrite_validates_and_is_a_noop_when_unchanged():
    note = okf.normalize_note(LEGACY_NOTE, actor="a/1")
    with pytest.raises(ValueError):
        okf.set_lifecycle_fields(note, status="archived")
    with pytest.raises(ValueError):
        okf.set_lifecycle_fields(note, stale_after="2027-01-01T00:00:00")  # no offset
    with pytest.raises(ValueError):
        okf.set_lifecycle_fields(note, verified=[{"at": "2026-01-01T00:00:00Z"}])
    with pytest.raises(ValueError):
        okf.set_lifecycle_fields("no frontmatter\n", status="draft")
    assert okf.set_lifecycle_fields(note, status=None) is note


# ---------------------------------------------------------------------------
# Writers: every path that puts a learning note on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    kb = tmp_path / "learnings"
    state = tmp_path / "state"
    (kb / "documents").mkdir(parents=True)
    state.mkdir()
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(state))
    monkeypatch.setattr(metrics, "METRICS_PATH", state / "metrics.jsonl")
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("no graph in tests")))
    return kb


def _reflect_add(path: Path) -> Path:
    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(path)])
    assert result.exit_code == 0, result.output
    docs = sorted(Path(os.environ["GLOBAL_LEARNINGS_PATH"], "documents").glob("*.md"))
    assert len(docs) == 1
    return docs[0]


def test_reflect_add_stores_a_conformant_note(kb_env, tmp_path):
    src = tmp_path / "legacy.md"
    src.write_text(LEGACY_NOTE.replace("id: lrn", "category: memories\nid: lrn"))
    stored = _reflect_add(src).read_text()
    fm = _assert_conformant(stored)
    assert fm["type"] == "learning"
    assert fm["generated"]["by"].startswith("reflect-cli/")
    assert fm["confidence_num"] == 0.9 and fm["causal_relations"][0]["type"] == "causes"
    assert _body(stored) == _body(src.read_text())
    assert src.read_text().startswith("---\n# reflect comment")  # source untouched


def test_drain_note_survives_reflect_add_byte_for_byte(kb_env, tmp_path):
    import drain_extract

    md = drain_extract.render_md({
        "title": "Pin llvmlite for numba", "category": "build-errors",
        "key_insight": "Pin llvmlite so uv never backtracks.", "rule": "pin it",
        "confidence_num": 0.9, "tags": ["uv"], "body": "## Problem\nbacktrack",
        "causal_relations": [{"source": "uv", "target": "llvmlite", "type": "causes"}],
    }, source_path="/tmp/s.jsonl", session_id="sid-1")
    fm = _assert_conformant(md)
    assert fm["generated"]["by"].startswith("reflect-drain/")
    assert fm["sources"] == [{"id": "source", "resource": "/tmp/s.jsonl"}]
    assert fm["description"] == "Pin llvmlite so uv never backtracks."

    src = tmp_path / "drain.md"
    src.write_text(md)
    assert _reflect_add(src).read_text() == md  # already conformant: stored verbatim


def _run_hook(hook: Path, payload: str, state: Path, learnings: Path):
    env = {**os.environ, "REFLECT_STATE_DIR": str(state),
           "REFLECT_LEARNINGS_DIR": str(learnings)}
    return subprocess.run([sys.executable, str(hook)], input=payload, capture_output=True,
                          text=True, env=env, timeout=30, check=False)


def _arm(state: Path, kind: str, sid: str, data: dict) -> None:
    armed = state / kind / f"{sid}.json"
    armed.parent.mkdir(parents=True, exist_ok=True)
    armed.write_text(json.dumps({**data, "ts": time.time()}))


@pytest.mark.parametrize("hook_layout", ["plugin", "no-scripts-dir"])
def test_hook_mini_and_permission_learnings_are_conformant(tmp_path, hook_layout):
    hook = HOOK
    if hook_layout == "no-scripts-dir":  # copilot layout: scripts/ does not resolve
        hook = tmp_path / "a" / "skills" / "recall" / "hooks" / HOOK.name
        hook.parent.mkdir(parents=True)
        shutil.copy(HOOK, hook)
    state, learnings = tmp_path / "state", tmp_path / "learnings"

    _arm(state, "armed", "sess-mini", {"tool": "Bash", "tool_input": "rm x",
                                       "tool_response": "denied"})
    r = _run_hook(hook, json.dumps({"session_id": "sess-mini",
                                    "prompt": "no, use trash: instead of rm"}),
                  state, learnings)
    assert r.returncode == 0, r.stderr
    _arm(state, "permission-armed", "sess-perm", {"tool": "WebFetch", "message": "ok?"})
    r = _run_hook(hook, json.dumps({"session_id": "sess-perm", "prompt": "yes always"}),
                  state, learnings)
    assert r.returncode == 0, r.stderr

    notes = sorted(learnings.glob("*.md"))
    assert [n.name.split("-")[1] for n in notes] == ["mini", "perm"]
    for note in notes:
        fm = _assert_conformant(note.read_text())
        assert fm["type"] == "learning" and fm["title"]
        assert fm["source"] in ("posttooluse-minilearning", "permission-pattern")
        if hook_layout == "plugin":
            assert fm["generated"]["by"].startswith("reflect-mini/")
            assert fm["sources"][0]["resource"].startswith("session:sess-")


@pytest.mark.parametrize("payload", ["", "not json", "[]", '{"session_id": 5, "prompt": null}',
                                     '{"session_id": "s\\n---\\ntype: x", "prompt": "no wrong"}'])
def test_hook_stays_exit_zero_on_bad_input(tmp_path, payload):
    state, learnings = tmp_path / "state", tmp_path / "learnings"
    _arm(state, "armed", "s\n---\ntype: x", {"tool": "Bash"})
    r = _run_hook(HOOK, payload, state, learnings)
    assert r.returncode == 0, r.stderr
    for note in learnings.glob("*.md"):
        _assert_conformant(note.read_text())


def test_fleet_import_is_conformant_and_survives_reimport(kb_env, tmp_path):
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "patterns.jsonl").write_text(json.dumps(
        {"title": "Retry with jitter", "pattern": "Use backoff with jitter. Avoids herds."}) + "\n")
    assert fleet_importer.ingest(root, ["patterns"]).imported == 1
    assert fleet_importer.ingest(root, ["patterns"]).deduped == 1  # rewrites occurrences

    (doc,) = (kb_env / "documents").glob("*.md")
    fm = _assert_conformant(doc.read_text())
    assert fm["occurrences"] == 2 and fm["quarantine"] is True
    assert fm["generated"]["by"].startswith("reflect-fleet-import/")
    assert fm["sources"][0]["resource"].endswith("patterns.jsonl")


@pytest.mark.parametrize("with_pyyaml", [True, False])
def test_skill_knowledge_note_is_conformant(tmp_path, monkeypatch, with_pyyaml):
    import output_generator

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    if not with_pyyaml:
        monkeypatch.setattr(output_generator, "yaml", None)
    path, _ = output_generator.create_knowledge_note(
        title="Quote YAML timestamps", category="build-errors", tags=["yaml"],
        symptoms=[], root_cause="bare timestamps parse", key_insight="Quote them.",
        problem="Schema rejected it.", solution="Quote it.", session_id="s1",
    )
    fm = _assert_conformant(path.read_text())
    assert fm["generated"]["by"].startswith("reflect-skill/")
    assert fm["key_insight"] == "Quote them." and fm["description"] == "Quote them."


def test_team_share_copy_is_conformant_source_untouched(tmp_path):
    src = tmp_path / "local" / "legacy.md"
    src.parent.mkdir()
    src.write_text(LEGACY_NOTE)
    (dest,) = write_flow._copy_into_team(src, tmp_path / "team")
    fm = _assert_conformant(dest.read_text())
    assert fm["generated"]["by"].startswith("reflect-share/")
    assert src.read_text() == LEGACY_NOTE


def test_serve_archive_sets_deprecated_and_restore_round_trips(tmp_path):
    kb_root = tmp_path / "kb"
    (kb_root / "documents").mkdir(parents=True)
    note = kb_root / "documents" / "lrn-legacy-abc123.md"
    original = okf.normalize_note(LEGACY_NOTE, actor="a/1")
    note.write_text(original)
    kb = KnowledgeBase(kb_root)

    kb.archive("lrn-legacy-abc123")
    archived = (kb_root / "archived" / note.name).read_text()
    assert _assert_conformant(archived)["status"] == "deprecated"
    assert _body(archived) == _body(original)

    kb.restore("lrn-legacy-abc123")
    assert note.read_text() == original


# ---------------------------------------------------------------------------
# Review regressions
# ---------------------------------------------------------------------------


def test_production_parsers_split_on_delimiter_lines_only():
    text = '---\ntype: learning\ntitle: "cost --- benefit"\n---\nbody --- here\n'
    fm = {"type": "learning", "title": "cost --- benefit"}
    assert learnings_cli.parse_frontmatter(text) == (fm, "body --- here")
    assert write_flow.parse_frontmatter(text) == (fm, "body --- here")


def test_descriptions_never_carry_a_dash_run():
    assert okf_profile.one_line("no --- use rg ----- ok -- fine") == "no - use rg - ok -- fine"
    out = okf.normalize_note("---\ntype: learning\n---\nUse rg --- not grep. Faster.\n",
                             actor="a/1")
    assert _assert_conformant(out)["description"] == "Use rg - not grep."


def test_hook_correction_with_dashes_keeps_metadata(tmp_path):
    state, learnings = tmp_path / "state", tmp_path / "learnings"
    _arm(state, "armed", "sess-dash", {"tool": "Bash", "tool_input": "grep ---x"})
    r = _run_hook(HOOK, json.dumps({"session_id": "sess-dash",
                                    "prompt": "no, use rg ---\n---\nnot grep"}),
                  state, learnings)
    assert r.returncode == 0, r.stderr
    (note,) = learnings.glob("lrn-mini-*.md")
    fm = _assert_conformant(note.read_text())
    assert "---" not in fm["description"] and fm["source"] == "posttooluse-minilearning"


def test_reflect_add_accepts_dashes_in_values_and_keeps_metadata(kb_env, tmp_path):
    src = tmp_path / "dash.md"
    src.write_text('---\ntitle: "rg --- vs grep"\ncategory: tools\n'
                   'key_insight: "Use rg --- not grep."\n---\nBody --- text.\n')
    fm = _assert_conformant(_reflect_add(src).read_text())
    assert fm["title"] == "rg --- vs grep" and fm["description"] == "Use rg - not grep."


def test_reflect_add_copies_conformant_note_bytes_and_mode(kb_env, tmp_path):
    src = tmp_path / "crlf.md"
    conformant = okf.normalize_note(LEGACY_NOTE, actor="a/1").replace("\n", "\r\n")
    src.write_bytes(conformant.replace("id: lrn", "category: memories\r\nid: lrn").encode())
    src.chmod(0o640)
    stored = _reflect_add(src)
    assert stored.read_bytes() == src.read_bytes()
    assert stored.stat().st_mode & 0o777 == 0o640


def test_reflect_add_normalizing_keeps_crlf_and_mode(kb_env, tmp_path):
    src = tmp_path / "legacy.md"
    src.write_bytes(LEGACY_NOTE.replace("id: lrn", "category: memories\nid: lrn")
                    .replace("\n", "\r\n").encode())
    src.chmod(0o600)
    stored = _reflect_add(src)
    raw = stored.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")  # no bare LF introduced
    assert stored.stat().st_mode & 0o777 == 0o600
    _assert_conformant(raw.decode())


def test_crlf_survives_normalize_and_lifecycle_edits():
    crlf = LEGACY_NOTE.replace("\n", "\r\n")
    out = okf.set_lifecycle_fields(okf.normalize_note(crlf, actor="a/1"), status="draft")
    assert out.count("\n") == out.count("\r\n")
    assert okf.parse_note(out)[0]["status"] == "draft"
    bare = okf.normalize_note("Plain body.\r\n", actor="a/1")
    assert bare.count("\n") == bare.count("\r\n")


def test_generated_at_is_deterministic_without_created(tmp_path):
    note = "---\ntype: learning\ntitle: t\n---\nbody\n"
    assert okf.parse_note(okf.normalize_note(note, actor="a/1"))[0]["generated"] == {"by": "a/1"}

    src = tmp_path / "local" / "undated.md"
    src.parent.mkdir()
    src.write_text(note)
    os.utime(src, (1767225600, 1767225600))  # 2026-01-01T00:00:00Z
    first = write_flow._copy_into_team(src, tmp_path / "team")[0].read_bytes()
    second = write_flow._copy_into_team(src, tmp_path / "team")[0].read_bytes()
    assert first == second
    fm = _assert_conformant(first.decode())
    assert fm["generated"]["at"] == "2026-01-01T00:00:00Z"


OBSERVATION = """\
---
type: observation
id: obs-team-prefers-rg
status: active
statement: "This team prefers rg over grep."
---

## Observation
"""


def test_serve_archive_leaves_observation_status_byte_identical(tmp_path):
    kb_root = tmp_path / "kb"
    (kb_root / "documents").mkdir(parents=True)
    note = kb_root / "documents" / "obs-team-prefers-rg.md"
    note.write_text(OBSERVATION)
    kb = KnowledgeBase(kb_root)

    kb.archive("obs-team-prefers-rg")
    assert (kb_root / "archived" / note.name).read_text() == OBSERVATION
    kb.restore("obs-team-prefers-rg")
    assert note.read_text() == OBSERVATION


def test_serve_archive_survives_a_failing_status_stamp(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    kb_root = tmp_path / "kb"
    (kb_root / "documents").mkdir(parents=True)
    note = kb_root / "documents" / "lrn-legacy-abc123.md"
    note.write_text(LEGACY_NOTE)
    kb = KnowledgeBase(kb_root)

    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(okf, "set_lifecycle_fields", boom)
    assert kb.archive("lrn-legacy-abc123")["archived"] is True
    assert (kb_root / "archived" / note.name).read_text() == LEGACY_NOTE
    assert all(m["id"] != "lrn-legacy-abc123" for m in kb.memories())  # invalidated
    logged = json.loads((tmp_path / "state" / "errors.json").read_text())["errors"]
    assert logged[0]["kind"] == "okf_status_stamp_failed"


# ---------------------------------------------------------------------------
# Re-review regressions
# ---------------------------------------------------------------------------


def _python(version: str) -> str | None:
    uv = shutil.which("uv")
    if not uv:
        return None
    found = subprocess.run([uv, "python", "find", "--no-project", version], capture_output=True,
                           text=True, check=False)
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


@pytest.mark.parametrize("version", ["3.9", "3.10"])
def test_drain_imports_and_renders_on_old_system_python(version):
    """reflect-drain-bg.sh runs drain_extract.py with the system python3 (3.9
    on macOS), so the vendored okf_profile must not need 3.11+."""
    python = _python(version)
    if python is None:
        pytest.skip(f"no Python {version} available")
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "import okf_profile, drain_extract\n"
        "md = drain_extract.render_md({'title': 't', 'key_insight': 'k \\U0001F600',"
        " 'body': '## P\\nx'}, source_path='/tmp/s', session_id='s')\n"
        "assert 'generated: {by: reflect-drain/' in md, md\n"
        "assert okf_profile.to_iso8601('2026-01-01T00:00:00Z') == '2026-01-01T00:00:00Z'\n"
        "assert okf_profile.has_offset('2026-01-01T10:00:00z')\n"
    )
    r = subprocess.run([python, "-c", probe, str(PLUGIN / "scripts")], capture_output=True,
                       text=True, check=False)
    assert r.returncode == 0, r.stderr


# Written by HEAD's output_generator with PyYAML missing (pre-OKF): the closing
# fence is glued to the last value, so there is no `---` delimiter line.
GLUED_FENCE_NOTE = (
    '---\ntitle: "Glued fence note"\ncategory: "build-errors"\ntags: [yaml]\n'
    'symptoms: [s]\nroot_cause: "r"\nkey_insight: "k"\ncreated: "2026-10-05"\n'
    'confidence: "high"\nconfidence_num: 0.9\nproblem: "p"\nprovenance:\n'
    '  source_tool: "claude"\n  source_path: "/tmp/s.jsonl"\n  session_id: "s1"\n'
    '  content_hash: "h"\n  detected_at: "2026-10-05T15:11:42.632412"---\n\n'
    "## Problem\n\np\n\n## Solution\n\ns\n"
)


def test_glued_fence_notes_are_still_read(kb_env):
    fm, body = learnings_cli.parse_frontmatter(GLUED_FENCE_NOTE)
    assert fm["title"] == "Glued fence note" and fm["provenance"]["content_hash"] == "h"
    assert body.startswith("## Problem")
    (kb_env / "documents" / "glued.md").write_text(GLUED_FENCE_NOTE)
    assert [d["title"] for d in learnings_cli.get_all_documents()] == ["Glued fence note"]
    with pytest.raises(ValueError):  # read-only: never rewritten in place
        okf.normalize_note(GLUED_FENCE_NOTE, actor="a/1")


def test_opening_fence_may_carry_a_comment():
    text = "--- # learning\ntype: learning\ntitle: t\n---\nbody\n"
    assert learnings_cli.parse_frontmatter(text) == ({"type": "learning", "title": "t"}, "body")
    out = okf.set_lifecycle_fields(text, status="draft")
    assert out.startswith("--- # learning\n") and okf.parse_note(out)[0]["status"] == "draft"


def test_hook_fallback_quoting_survives_emoji_dashes_and_newlines(tmp_path):
    hook = tmp_path / "a" / "skills" / "recall" / "hooks" / HOOK.name  # no scripts/
    hook.parent.mkdir(parents=True)
    shutil.copy(HOOK, hook)
    state, learnings = tmp_path / "state", tmp_path / "learnings"
    _arm(state, "armed", "sess-fb", {"tool": "Bash\n---\ntype: x"})
    prompt = "no, use rg \U0001F600 ---\n---\nnot grep " + "x" * 400
    r = _run_hook(hook, json.dumps({"session_id": "sess-fb", "prompt": prompt}), state, learnings)
    assert r.returncode == 0, r.stderr

    (note,) = learnings.glob("lrn-mini-*.md")
    text = note.read_bytes().decode("utf-8")
    fm = _assert_conformant(text)
    assert "\U0001F600" in fm["description"] and "---" not in fm["description"]
    assert len(fm["description"]) <= 200 and "\n" not in fm["description"]
    assert yaml.safe_dump(fm, allow_unicode=True).encode("utf-8")  # no lone surrogates
    assert "generated" not in fm  # fallback renderer: `type` only, by design


def _archive_round_trip(tmp_path, note_text: str) -> tuple[str, str]:
    kb_root = tmp_path / "kb"
    (kb_root / "documents").mkdir(parents=True)
    note = kb_root / "documents" / "lrn-legacy-abc123.md"
    note.write_text(note_text)
    kb = KnowledgeBase(kb_root)
    kb.archive("lrn-legacy-abc123")
    archived = (kb_root / "archived" / note.name).read_text()
    kb.restore("lrn-legacy-abc123")
    assert not list((kb_root / "archived").glob("*.okf-prior-status"))
    return archived, note.read_text()


@pytest.mark.parametrize("prior", ["deprecated", "draft", "stable"])
def test_serve_restore_puts_back_the_prior_status(tmp_path, prior):
    original = okf.set_lifecycle_fields(okf.normalize_note(LEGACY_NOTE, actor="a/1"),
                                        status=prior)
    archived, restored = _archive_round_trip(tmp_path, original)
    assert okf.parse_note(archived)[0]["status"] == "deprecated"
    assert restored == original
