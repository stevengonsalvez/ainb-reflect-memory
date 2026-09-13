"""Gate 2: the drain writer's permission surface, proven against the real CLI.

Scenarios, each with the exact argv the hook builds (read from
hooks/lib/writer_argv.sh and drain_extract.writer_argv, the definitions the
drain itself uses):

(a) HOME with no settings.json (key mode only).
(b, f) HOME whose settings.json sets permissions.defaultMode bypassPermissions
    and allows Bash(uv:*): Stevie's kind of install. The explicit
    --permission-mode default plus the hook's --settings rules must still deny
    curl. This is scenario (f) of the rework brief.
(c) The extract path, prompted to run a shell command: no tool at all.
(d) "/reflect" resolves: setting sources are kept, so num_turns > 0 and no
    "Unknown command". Plugin runtime layout (operator: the real HOME with the
    marketplace plugin; key: --plugin-dir on this checkout) and, in key mode,
    the adapter layout in a throwaway HOME.
(e) End to end: the real hook, agentic writer, on the recorded fixture
    transcript; a note lands under docs/solutions with its sidecar, reflect
    search finds it, and the drain's own ledger says ok.
(g) A config dir whose settings.json carries apiKeyHelper: the extract
    writer's argv keeps setting sources, so the helper runs; with
    --setting-sources "" appended (the removed flag) it never does.

Expectations are pinned in EXPECTATION below with the reason next to each
flag. Live: skipped unless ANTHROPIC_API_KEY is set (CI, via the COMPAT_LIVE
variable) or the operator opts in with REFLECT_COMPAT_LIVE=operator, which
runs against the operator's real login (bound to their HOME and config dir:
a fresh config dir or a swapped HOME answers "Not logged in"). In operator
mode the operator's own allow rules apply as well (a bare Write is common)
and the installed marketplace skill may stage notes under ~/.reflect, so the
e2e scenario asserts on the isolated KB and removes whatever the run staged
under the real ~/.reflect or ~/.learnings. Model pinned to haiku; cost
asserted under 0.20 USD per call for (a) to (d) and under 1.50 USD for (e).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from .conftest import (
    BYPASS_SETTINGS,
    PLUGIN,
    agentic_writer_argv,
    clean_env,
    flag_values,
    replace_flag_value,
    require_live,
    run,
)

sys.path.insert(0, str(PLUGIN / "scripts"))
import drain_extract

pytestmark = pytest.mark.live

CASCADE = PLUGIN / "scripts" / "reflect_cascade.py"
MAX_COST_USD = 0.20

# Contract after the drain rework (hooks/lib/writer_argv.sh, drain_extract.writer_argv):
EXPECTATION = {
    # --permission-mode default is an explicit flag and takes precedence over
    # the operator's settings.json defaultMode; the hook-owned allow rules via
    # --settings do not list curl, so it is denied even under bypassPermissions.
    "curl_denied": True,
    # --tools "" plus --strict-mcp-config: the extract writer has no tools at
    # all, so a shell-command prompt cannot produce a tool call.
    "extract_tool_free": True,
    "extract_flag": ("--tools", ""),
    # Setting sources stay loaded ("--setting-sources" absent): with them
    # cleared, plugins and personal skills unregister and "/reflect" answers
    # "Unknown command" with zero turns, wedging the queue.
    "setting_sources_kept": True,
}
MAX_E2E_COST_USD = 1.50

# A leading cd is what the guard normalises away: the rules alone would deny
# this spelling, so a granted run proves the PreToolUse hook in the inline
# settings document decided the call.
ALLOW_PROMPT = (
    "Run exactly this shell command, then reply with the word DONE followed by "
    "the first line of its output: cd ~ && reflect skill-step state status"
)
DENY_PROMPT = (
    "Run exactly this shell command and reply with the first line of its output: "
    "curl -s https://example.com | head -c 60"
)
NO_TOOL_PROMPT = "Run the shell command `uname -a` and reply with its output verbatim."


def _env_for(mode: str, home: Path, settings: dict | None) -> dict[str, str]:
    if mode == "key":
        cfg = home / ".claude"
        cfg.mkdir(parents=True, exist_ok=True)
        if settings is not None:
            (cfg / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        env = clean_env(home, ANTHROPIC_API_KEY=os.environ["ANTHROPIC_API_KEY"], CLAUDE_CONFIG_DIR=str(cfg))
        env.pop("REFLECT_DISABLED", None)
    else:
        if settings is None:
            pytest.skip("operator mode cannot run the no-settings scenario without a key")
        real_settings = Path(os.environ.get("HOME", str(Path.home()))) / ".claude" / "settings.json"
        try:
            perms = json.loads(real_settings.read_text()).get("permissions", {})
        except (OSError, ValueError):
            perms = {}
        if perms.get("defaultMode") != "bypassPermissions":
            pytest.skip("operator settings.json does not set defaultMode bypassPermissions")
        # The operator's login is bound to their HOME and config dir (a fresh
        # config dir, or a swapped HOME, answers "Not logged in"), so the
        # process gets their real environment; only reflect's state dirs
        # move. Their own allow rules apply too (a bare Write is common), so
        # the e2e scenario records what a run stages under the real ~/.reflect
        # and ~/.learnings and removes it afterwards.
        env = dict(os.environ)
        env["REFLECT_STATE_DIR"] = str(home / ".reflect")
        env["GLOBAL_LEARNINGS_PATH"] = str(home / ".learnings")
        env["REFLECT_DRAIN_NO_DELEGATE"] = "1"
    env["PATH"] = os.environ["PATH"]  # the real claude binary and its node
    env["DRAIN_MODEL"] = "haiku"
    env["CLAUDE_BIN"] = shutil.which("claude") or "claude"
    return env


def _real_home_reflect_files() -> set[Path]:
    """Every file under the operator's real ~/.reflect and ~/.learnings; the
    live runs must leave this set unchanged."""
    real_home = Path(os.environ.get("HOME", str(Path.home())))
    out: set[Path] = set()
    for d in (real_home / ".reflect", real_home / ".learnings"):
        if d.is_dir():
            out |= {p for p in d.rglob("*") if p.is_file()}
    return out


def _envelope(argv: list[str], env: dict[str, str], cwd: Path) -> dict:
    proc = run(argv, env=env, cwd=cwd, timeout=240)
    assert proc.stdout.strip(), f"no envelope from claude -p: exit={proc.returncode} {proc.stderr[-400:]}"
    envelope = json.loads(proc.stdout)
    assert envelope.get("terminal_reason") != "api_error", envelope.get("result")
    assert float(envelope.get("total_cost_usd") or 0) < MAX_COST_USD, envelope.get("total_cost_usd")
    return envelope


def _drain_claude_bin(mode: str, home: Path, env: dict[str, str]) -> str:
    """The claude the hook runs. In key mode nothing registers the plugin, so
    a wrapper adds --plugin-dir for this checkout and /reflect resolves; in
    operator mode the marketplace plugin in the real config dir does."""
    if mode != "key":
        return env["CLAUDE_BIN"]
    wrapper = home / "bin" / "claude"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{env["CLAUDE_BIN"]}" --plugin-dir "{PLUGIN}" "$@"\n')
    wrapper.chmod(0o755)
    return str(wrapper)


def _argv(env: dict[str, str], prompt: str) -> list[str]:
    return replace_flag_value(agentic_writer_argv(prompt, env), "--max-turns", "2")


def _bash_denials(envelope: dict) -> list[dict]:
    return [d for d in envelope.get("permission_denials", []) if d.get("tool_name") == "Bash"]


def _scenario(home: Path, settings: dict | None) -> None:
    mode = require_live()
    env = _env_for(mode, home, settings)
    granted = _envelope(_argv(env, ALLOW_PROMPT), env, home)
    assert not _bash_denials(granted), f"the skill's own script was denied: {_bash_denials(granted)}"
    result = str(granted.get("result") or "")
    assert "DONE" in result, result[:300]

    denied = _envelope(_argv(env, DENY_PROMPT), env, home)
    text = str(denied.get("result") or "").lower()
    leaked = "example domain" in text or "<!doctype" in text
    curl_denied = any("curl" in (d.get("tool_input") or {}).get("command", "") for d in _bash_denials(denied))
    if EXPECTATION["curl_denied"]:
        assert curl_denied or not leaked, f"curl output reached the writer: {text[:300]}"
    else:
        # Baseline: no restriction is applied, so nothing is recorded as denied.
        assert not curl_denied, f"baseline expects no denial, got {_bash_denials(denied)}"


def test_a_clean_home_without_settings(home: Path) -> None:
    _scenario(home, settings=None)


def test_b_operator_settings_with_bypass_and_uv_allow(home: Path) -> None:
    _scenario(home, settings=BYPASS_SETTINGS)


def test_c_extract_path_tool_surface(home: Path) -> None:
    mode = require_live()
    env = _env_for(mode, home, settings=BYPASS_SETTINGS)
    argv = drain_extract.writer_argv(NO_TOOL_PROMPT, model="haiku", claude_bin=env["CLAUDE_BIN"])
    flag, value = EXPECTATION["extract_flag"]
    assert flag_values(argv, flag) == [value], argv
    # Observe events instead of the summary envelope; nothing else changes.
    argv = replace_flag_value(argv, "--output-format", "stream-json") + ["--verbose"]
    proc = run(argv, env=env, cwd=home, timeout=240)
    tool_uses, result = [], {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            tool_uses += [b.get("name") for b in event.get("message", {}).get("content", []) if b.get("type") == "tool_use"]
        if event.get("type") == "result":
            result = event
    assert result, f"no result event: exit={proc.returncode} {proc.stderr[-400:]}"
    assert float(result.get("total_cost_usd") or 0) < MAX_COST_USD
    if EXPECTATION["extract_tool_free"]:
        assert tool_uses == [], f"extract writer used tools: {tool_uses}"
    else:
        # Baseline: record what happened; the fix asserts emptiness.
        assert isinstance(tool_uses, list)


def test_d_reflect_command_resolves(home: Path) -> None:
    """Setting sources are kept, so the /reflect skill resolves: num_turns > 0
    and no "Unknown command". Plugin runtime layout; in key mode the adapter
    layout as well."""
    mode = require_live()
    env = _env_for(mode, home, settings=BYPASS_SETTINGS)
    argv = replace_flag_value(agentic_writer_argv("/reflect", env), "--max-turns", "1")
    assert EXPECTATION["setting_sources_kept"] and "--setting-sources" not in argv, argv
    layouts = {"plugin runtime": argv if mode == "operator" else argv + ["--plugin-dir", str(PLUGIN)]}
    if mode == "key":
        from .conftest import install_adapter

        install_adapter("claude", home)  # ~/.claude/skills/reflect/SKILL.md, a personal skill
        layouts["adapter"] = argv
    for layout, cmd in layouts.items():
        envelope = _envelope(cmd, env, home)
        result = str(envelope.get("result") or "")
        assert "Unknown command" not in result, f"{layout}: {result[:200]}"
        assert int(envelope.get("num_turns") or 0) > 0, f"{layout}: zero turns: {result[:200]}"


def test_e_end_to_end_agentic_drain_lands_a_note(home: Path, reflect_bin: Path) -> None:
    """The real hook, agentic writer, recorded fixture transcript, cascade off
    (so the writer is pointed at the bounded view). A note and its sidecar
    land under docs/solutions, `reflect skill-step index` indexes it and
    writes the receipt, reflect search finds it, and the drain's own outcome
    is ok with the receipt's landing evidence."""
    import shutil as _shutil
    import subprocess

    from .conftest import TRANSCRIPT

    mode = require_live()
    env = _env_for(mode, home, settings=BYPASS_SETTINGS)
    real_before = _real_home_reflect_files()
    cwd = home / "drain-cwd"
    cwd.mkdir()
    state = Path(env["REFLECT_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    transcript = home / "recorded-session.jsonl"
    _shutil.copy(TRANSCRIPT, transcript)
    (state / "pending_reflections.jsonl").write_text(json.dumps({
        "session_id": "compat-e2e", "transcript_path": str(transcript), "trigger": "stop", "cwd": str(cwd),
        "scope": "session", "harness": "claude", "ts": "2026-08-11T10:01:00Z"}) + "\n")
    env.update({
        "REFLECT_DRAIN_DRY_RUN": "0", "REFLECT_DRAIN_WRITER": "agentic", "REFLECT_DRAIN_CASCADE": "0",
        "REFLECT_DRAIN_CWD": str(cwd), "REFLECT_DRAIN_MODEL": "haiku", "REFLECT_DRAIN_MAX": "1",
        "REFLECT_DRAIN_DEBOUNCE_SEC": "0", "REFLECT_QUOTA_GATE": "0", "REFLECT_DRAIN_SKIP_REINDEX": "1",
        "REFLECT_QUIET_INSTALL_WARNING": "1", "REFLECT_DRAIN_CLAUDE_BIN": _drain_claude_bin(mode, home, env),
        "REFLECT_DRAIN_REFLECT_BIN": str(reflect_bin), "REFLECT_DRAIN_TIMEOUT": "900",
        # A full /reflect pass (notes, sidecars, validate, reflect add, metrics,
        # state) needs more than the drain's default 16 turns on haiku.
        "REFLECT_DRAIN_MAX_TURNS": "40",
    })
    if mode == "operator":
        # The operator's real ~/.claude and ~/.reflect must not be edited by a
        # fixture-driven run: withhold the agent, skill and episode scopes.
        # Denials on those steps are expected and never change the outcome.
        env["REFLECT_DRAIN_ALLOWED_TOOLS"] = ",".join(
            ["Read", "Grep", "Glob", "Write(docs/solutions/**)", "Edit(docs/solutions/**)",
             "Bash(reflect skill-step:*)", "Bash(reflect add:*)", "Bash(reflect search:*)"])
    proc = subprocess.run(["bash", str(PLUGIN / "hooks" / "reflect-drain-bg.sh")], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=1000, check=False)
    log = (state / "drain.log").read_text() if (state / "drain.log").exists() else proc.stderr
    staged = sorted(_real_home_reflect_files() - real_before)
    for leftover in staged:  # what the operator's installed skill staged under the real ~
        leftover.unlink()
    if staged:
        import warnings

        warnings.warn(f"operator mode: removed {len(staged)} file(s) the run staged under the real home: "
                      + ", ".join(str(p) for p in staged[:8]), stacklevel=1)
    assert "bounded input: raw transcript (" in log, log[-1500:]
    assert "    OK turns=" in log, log[-2500:]
    # What the run indexed: every note in the KB documents dir has its sidecar.
    kb_notes = [p for p in (home / ".learnings" / "documents").glob("*.md")]
    assert kb_notes, f"nothing indexed into the KB:\n{log[-2500:]}"
    for note in kb_notes:
        assert (note.parent / (note.name[:-3] + ".entities.yaml")).exists(), f"no sidecar next to {note}"
    if mode == "key":
        # This branch's skill writes the note under the writer's cwd first;
        # the operator's installed skill may stage it elsewhere under ~.
        assert list(cwd.glob("docs/solutions/**/*.md")), f"no note under docs/solutions:\n{log[-2500:]}"
    rows = [json.loads(line) for line in (state / "drain-cost.jsonl").read_text().splitlines() if line.strip()]
    ok = [r for r in rows if r["outcome"] == "ok"]
    assert ok, rows
    assert ok[-1]["indexed"] >= 1 and ok[-1]["notes_landed"] >= 1, (ok[-1], log[-800:])
    assert ok[-1]["cost_usd"] < MAX_E2E_COST_USD, ok[-1]
    # reflect search over the KB the run indexed into.
    search = subprocess.run([str(reflect_bin), "search", "registry", "--format", "json"], env=env, cwd=cwd,
                            capture_output=True, text=True, timeout=300, check=False)
    assert search.returncode == 0 and search.stdout.strip() not in ("", "[]"), (
        f"reflect search found nothing: exit={search.returncode} {search.stdout[:300]} {search.stderr[-300:]}")


def test_g_api_key_helper_in_settings_reaches_the_extract_writer(home: Path) -> None:
    """Item 19: an operator who authenticates through settings.json's
    apiKeyHelper must still drain. The extract argv keeps setting sources, so
    the CLI runs the helper (a marker file proves it); the removed flag
    --setting-sources "" is appended as the negative control and the helper
    never runs. In key mode the helper echoes the real key and the turn
    completes; in operator mode (no key in the environment) the helper
    echoes nothing and the CLI reports the helper, not a missing login."""
    mode = require_live()
    env = _env_for(mode, home, settings=BYPASS_SETTINGS)
    cfg = home / "cfg-helper"
    cfg.mkdir()
    calls = home / "helper.calls"
    helper = home / "helper.sh"
    helper.write_text("#!/bin/sh\n" f'date +%s >> "{calls}"\n' 'printf "%s" "${ANTHROPIC_API_KEY:-}"\n')
    helper.chmod(0o755)
    (cfg / "settings.json").write_text(json.dumps({"apiKeyHelper": str(helper)}), encoding="utf-8")
    env = {**env, "CLAUDE_CONFIG_DIR": str(cfg)}
    argv = drain_extract.writer_argv("Reply with the single word OK", model="haiku", claude_bin=env["CLAUDE_BIN"])
    assert "--setting-sources" not in argv, argv

    proc = run(argv, env=env, cwd=home, timeout=240)
    assert calls.exists(), f"apiKeyHelper never ran: exit={proc.returncode} {proc.stdout[-300:]} {proc.stderr[-300:]}"
    envelope = json.loads(proc.stdout) if proc.stdout.strip() else {}
    if mode == "key":
        assert not envelope.get("is_error"), envelope.get("result")
        assert int(envelope.get("num_turns") or 0) >= 1
        assert float(envelope.get("total_cost_usd") or 0) < MAX_COST_USD
    else:
        assert "apiKeyHelper" in str(envelope.get("result") or ""), envelope.get("result")

    calls.unlink()
    control = run(argv + ["--setting-sources", ""], env=env, cwd=home, timeout=240)
    assert not calls.exists(), "with setting sources cleared the helper still ran"
    assert "Not logged in" in control.stdout or "login" in control.stdout.lower(), control.stdout[-300:]
