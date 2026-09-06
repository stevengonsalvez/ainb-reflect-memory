"""Gate 2: the drain writer's permission surface, proven against the real CLI.

Three scenarios, each with the exact argv the hook builds (read from
hooks/lib/writer_argv.sh and drain_extract.writer_argv, the definitions the
drain itself uses):

(a) HOME with no settings.json.
(b) HOME whose settings.json sets permissions.defaultMode bypassPermissions
    and allows Bash(uv:*): Stevie's kind of install.
(c) The extract path, prompted to run a shell command.

Expectations are pinned in EXPECTATION below and describe the BASELINE on
main: the hook runs with bypassPermissions and no allowlist, so nothing is
restricted, the extract path passes --allowedTools "" (which does not remove
tools), and the operator's settings are inherited. The stack's drain fix
flips these expectations to denial and states why in the same table.

Live: skipped unless ANTHROPIC_API_KEY is set (CI, via the COMPAT_LIVE
variable) or the operator opts in with REFLECT_COMPAT_LIVE=operator, which
runs (b) and (c) against the operator's real login (a throwaway HOME cannot
hold that login). Model pinned to haiku through REFLECT_DRAIN_MODEL, the only
knob changed; cost asserted under 0.20 USD per run; --max-turns 2.
"""

from __future__ import annotations

import json
import os
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

# Baseline contract (main). The drain fix on the stack changes these and
# names the reason next to each flag.
EXPECTATION = {
    # main: --permission-mode bypassPermissions, no allowlist: curl is not denied.
    "curl_denied": False,
    # main: the extract writer passes --allowedTools "", which does not remove
    # tools; under bypassPermissions it may still run Bash.
    "extract_tool_free": False,
    "extract_flag": ("--allowedTools", ""),
}

ALLOW_PROMPT = (
    "Run exactly this shell command, then reply with the word DONE followed by "
    f"the first line of its output: python3 {CASCADE} --help"
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
        # The operator's login lives in their config dir and keychain, so the
        # process gets their real environment; only reflect's state dirs move.
        env = dict(os.environ)
        env["REFLECT_STATE_DIR"] = str(home / ".reflect")
        env["GLOBAL_LEARNINGS_PATH"] = str(home / ".learnings")
        env["REFLECT_DRAIN_NO_DELEGATE"] = "1"
    env["PATH"] = os.environ["PATH"]  # the real claude binary and its node
    env["DRAIN_MODEL"] = "haiku"
    env["CLAUDE_BIN"] = shutil.which("claude") or "claude"
    return env


def _envelope(argv: list[str], env: dict[str, str], cwd: Path) -> dict:
    proc = run(argv, env=env, cwd=cwd, timeout=240)
    assert proc.stdout.strip(), f"no envelope from claude -p: exit={proc.returncode} {proc.stderr[-400:]}"
    envelope = json.loads(proc.stdout)
    assert envelope.get("terminal_reason") != "api_error", envelope.get("result")
    assert float(envelope.get("total_cost_usd") or 0) < MAX_COST_USD, envelope.get("total_cost_usd")
    return envelope


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
    assert "usage: reflect_cascade.py" in result or "DONE" in result, result[:300]

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
