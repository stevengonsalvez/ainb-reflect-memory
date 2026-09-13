"""Tests for the analyze step (LLM -> candidate issues), with injected runner."""

from __future__ import annotations

import json
import subprocess

from reflect_kb.issues.analyze import analyze


def _runner_returning(stdout: str):
    def run(cmd):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run


def test_empty_timelines_short_circuit():
    cands, reason = analyze([], runner=_runner_returning("[]"))
    assert cands == []
    assert reason == "no-timelines"


def test_parses_bare_json_array():
    payload = json.dumps(
        [
            {"title": "Bug A", "body": "## Summary\nx", "labels": ["bug", "cli"]},
        ]
    )
    cands, reason = analyze(["timeline"], runner=_runner_returning(payload))
    assert reason == "ok"
    assert len(cands) == 1
    assert cands[0].title == "Bug A"
    assert cands[0].labels == ["bug", "cli"]


def test_parses_json_wrapped_in_claude_envelope():
    inner = json.dumps([{"title": "Bug B", "body": "b"}])
    envelope = json.dumps({"result": inner})
    cands, reason = analyze(["timeline"], runner=_runner_returning(envelope))
    assert reason == "ok"
    assert cands[0].title == "Bug B"


def test_extracts_array_from_surrounding_prose():
    noisy = 'Here are findings:\n[{"title": "Bug C", "body": "c"}]\nDone.'
    cands, reason = analyze(["timeline"], runner=_runner_returning(noisy))
    assert reason == "ok"
    assert cands[0].title == "Bug C"


def test_drops_items_missing_title_or_body():
    payload = json.dumps(
        [
            {"title": "", "body": "x"},
            {"title": "y", "body": ""},
            {"title": "Valid", "body": "ok"},
        ]
    )
    cands, reason = analyze(["timeline"], runner=_runner_returning(payload))
    assert [c.title for c in cands] == ["Valid"]


def test_unparseable_output_degrades():
    cands, reason = analyze(["timeline"], runner=_runner_returning("totally not json"))
    assert cands == []
    assert reason == "unparseable"


def test_runner_error_degrades_gracefully():
    def boom(cmd):
        raise FileNotFoundError("claude missing")

    cands, reason = analyze(["timeline"], runner=boom)
    assert cands == []
    assert reason.startswith("claude-error")


def test_analyzer_runs_claude_without_tools():
    """The prompt carries untrusted transcript text: no tools, no MCP, one turn,
    explicit permission mode (the same structural flags as the drain's extract writer)."""
    from reflect_kb.issues.analyze import analyzer_argv

    seen: list[list[str]] = []

    def capture(cmd, **_):
        seen.append(list(cmd))
        return _runner_returning("[]")(cmd)

    analyze(["timeline"], runner=capture)
    assert seen, "runner not called"
    argv = seen[0]
    assert argv[:2] == ["claude", "-p"] and argv == analyzer_argv(argv[2], argv[argv.index("--model") + 1])
    for flag, value in (("--tools", ""), ("--max-turns", "1"), ("--permission-mode", "default")):
        assert argv[argv.index(flag) + 1] == value, argv
    assert "--strict-mcp-config" in argv and "bypassPermissions" not in argv
    assert "--setting-sources" not in argv  # apiKeyHelper and the env block must load



def test_default_runner_marks_the_child_as_nested(monkeypatch) -> None:
    """The analyzer's claude -p keeps the operator's setting sources (hooks
    included); REFLECT_NESTED=1 makes every reflect hook exit at once, so the
    analyzer never queues or drains a reflection of its own."""
    import subprocess

    from reflect_kb.issues import analyze as mod

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._default_runner(["claude", "-p", "x"], timeout=5)
    assert seen["env"]["REFLECT_NESTED"] == "1" and seen["env"].get("PATH")
