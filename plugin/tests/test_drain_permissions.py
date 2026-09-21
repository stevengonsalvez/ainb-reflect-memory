"""The drain writer's permission surface, and what a denial does to a run.

The argv, rules, guard hook and prompt addendum come from the library the
hook sources (hooks/lib/writer_argv.sh); the extract writer's argv from
drain_extract.writer_argv. Every tool call the /reflect skill's steps name
must be covered by exactly one allow rule or listed as deliberately
excluded, on both layouts (plugin runtime and adapter install), and the
rules carry no path: every scripted step is `reflect skill-step <step>`.
"""

from __future__ import annotations

import fnmatch
import json
import shlex
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_LIB = _PLUGIN / "hooks" / "lib" / "writer_argv.sh"
_DRAIN = _PLUGIN / "hooks" / "reflect-drain-bg.sh"
_SKILL = _PLUGIN / "skills" / "reflect" / "SKILL.md"
_GUARD = _PLUGIN / "scripts" / "drain_guard.py"
sys.path.insert(0, str(_PLUGIN / "scripts"))
import drain_extract
import drain_guard

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
BASH_RULES = {"Bash(reflect skill-step:*)", "Bash(reflect add:*)", "Bash(reflect search:*)"}


def _lib(fn: str, *args: str, lib: Path = _LIB, **env: str) -> list[str]:
    """Source the lib, call ``fn``, return its NUL-separated output or array."""
    body = {
        "argv": 'drain_agentic_writer_argv "$2"; printf "%s\\0" "${WRITER_ARGV[@]}"',
        "rules": 'drain_writer_rules; printf "%s\\0" "${WRITER_RULES[@]}"',
        "settings": 'drain_writer_rules; drain_writer_settings_json; printf "\\0"',
        "prompt": 'drain_writer_prompt "$2"; printf "\\0"',
        "scripts_dir": 'drain_writer_scripts_dir; printf "\\0"',
        "guard": 'drain_writer_guard; printf "\\0"',
        "excluded": 'printf "%s\\0" "$_WRITER_EXCLUDED"',
        "defaults": 'printf "%s\\0%s\\0" "$DRAIN_DEFAULT_MODEL" "$DRAIN_DEFAULT_MAX_TURNS"',
    }[fn]
    proc = subprocess.run(["bash", "-c", f'source "$1"; {body}', "x", str(lib), *args],
                          env={**os.environ, **env}, capture_output=True, check=True, timeout=30)
    parts = proc.stdout.split(b"\0")
    assert parts[-1] == b""
    return [p.decode() for p in parts[:-1]]


def _flag(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# --------------------------------------------------------------------------- #
# argv shape
# --------------------------------------------------------------------------- #

def test_agentic_writer_pins_mode_rules_and_guard_but_keeps_setting_sources() -> None:
    argv = _lib("argv", "p")
    assert _flag(argv, "--permission-mode") == "default"
    assert "--strict-mcp-config" in argv
    # Setting sources stay loaded: dropping them unregisters plugins and
    # personal skills, and "/reflect" becomes "Unknown command" (zero turns).
    assert "--setting-sources" not in argv
    assert "bypassPermissions" not in argv and "--allowedTools" not in argv
    settings = json.loads(_flag(argv, "--settings"))
    assert settings["permissions"]["defaultMode"] == "default"
    rules = settings["permissions"]["allow"]
    assert rules == _lib("rules")
    assert not {"Bash", "Bash(*)", "Write", "Edit"} & set(rules)
    assert {r for r in rules if r.startswith("Bash(")} == BASH_RULES
    assert not any("/" in r for r in rules if r.startswith("Bash(")), "a Bash rule carries a path"
    # The guard decides every Bash call before it runs, from the same document.
    hooks = settings["hooks"]["PreToolUse"]
    assert len(hooks) == 1 and "Bash" in hooks[0]["matcher"].split("|")
    command = shlex.split(hooks[0]["hooks"][0]["command"])
    assert command[:2] == ["python3", _lib("guard")[0]] and Path(_lib("guard")[0]).is_file()
    # The guard is told exactly the prefixes the Bash rules grant.
    assert {command[i + 1] for i, a in enumerate(command) if a == "--allow"} == {
        r[len("Bash("):-len(":*)")] for r in BASH_RULES}
    assert Path(_lib("guard")[0]) == _GUARD.resolve()


def test_model_and_turn_defaults_have_one_home() -> None:
    model, turns = _lib("defaults")
    argv = _lib("argv", "p")
    assert _flag(argv, "--model") == model and _flag(argv, "--max-turns") == turns
    hook = _DRAIN.read_text()
    assert 'MAX_TURNS="${REFLECT_DRAIN_MAX_TURNS:-$DRAIN_DEFAULT_MAX_TURNS}"' in hook
    assert 'DRAIN_MODEL="${REFLECT_DRAIN_MODEL:-$DRAIN_DEFAULT_MODEL}"' in hook
    assert not re.search(r'MAX_TURNS="\$\{REFLECT_DRAIN_MAX_TURNS:-\d', hook)
    assert _flag(_lib("argv", "p", DRAIN_MODEL="haiku", MAX_TURNS="3"), "--model") == "haiku"


def test_allow_rules_override_replaces_the_list_and_narrows_the_guard() -> None:
    argv = _lib("argv", "p", REFLECT_DRAIN_ALLOWED_TOOLS="Read,Bash(reflect add:*)")
    settings = json.loads(_flag(argv, "--settings"))
    assert settings["permissions"]["allow"] == ["Read", "Bash(reflect add:*)"]
    assert "Bash" in settings["hooks"]["PreToolUse"][0]["matcher"].split("|")
    command = shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    assert command[2:] == ["--allow", "reflect add"]
    assert _flag(argv, "--permission-mode") == "default"  # the mode is never overridable
    # A search the override dropped is denied by the guard it installs.
    proc = subprocess.run([sys.executable, *command[1:]], capture_output=True, text=True, timeout=30,
                          input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "reflect search x"}}))
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # No Bash rule at all: the guard stays (it also denies credential paths)
    # but allows no command, so nothing gets past the rules.
    no_bash = json.loads(_flag(_lib("argv", "p", REFLECT_DRAIN_ALLOWED_TOOLS="Read,Grep"), "--settings"))
    assert no_bash["permissions"]["allow"] == ["Read", "Grep"]
    bare = shlex.split(no_bash["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    assert bare[2:] == ["--no-bash"]
    proc = subprocess.run([sys.executable, *bare[1:]], capture_output=True, text=True, timeout=30,
                          input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "reflect add docs/solutions/x.md"}}))
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# --------------------------------------------------------------------------- #
# the guard: credential paths and skill/agent frontmatter, for every path tool
# --------------------------------------------------------------------------- #

_HOME = str(Path.home())


@pytest.mark.parametrize("tool,tool_input,decision", [
    # credential stores, whatever the spelling
    ("Read", {"file_path": f"{_HOME}/.ssh/id_rsa"}, "deny"),
    ("Read", {"file_path": "docs/solutions/../.ssh/config"}, "deny"),  # a walk out
    ("Read", {"file_path": f"{_HOME}/.aws/credentials"}, "deny"),
    ("Read", {"file_path": f"{_HOME}/.config/gh/hosts.yml"}, "deny"),
    ("Read", {"file_path": f"{_HOME}/.claude/.credentials.json"}, "deny"),
    ("Read", {"file_path": f"{_HOME}/.claude/projects/p/session.jsonl"}, "deny"),  # raw transcripts
    ("Read", {"file_path": ".env.local"}, "deny"),
    ("Read", {"file_path": f"{_HOME}/certs/server.pem"}, "deny"),
    ("Grep", {"path": f"{_HOME}/.aws"}, "deny"),
    ("Glob", {"path": f"{_HOME}/.gnupg"}, "deny"),
    # a skill or agent file's frontmatter, where a planted hook would persist
    ("Edit", {"file_path": f"{_HOME}/.claude/agents/backend-developer.md",
              "old_string": "x", "new_string": "hooks:\n  PreToolUse: evil"}, "deny"),
    ("Edit", {"file_path": f"{_HOME}/.claude/skills/publish/SKILL.md",
              "old_string": "---\nname: publish", "new_string": "---\nname: publish"}, "deny"),
    ("Edit", {"file_path": f"{_HOME}/.claude/skills/publish/SKILL.md",
              "old_string": "a", "new_string": "permissionMode: bypassPermissions"}, "deny"),
    ("Write", {"file_path": f"{_HOME}/.claude/skills/publish/SKILL.md", "content": "x"}, "deny"),
    ("MultiEdit", {"file_path": f"{_HOME}/.claude/skills/publish/SKILL.md",
                   "edits": [{"old_string": "b", "new_string": "c"},
                             {"old_string": "d", "new_string": "allowed-tools: Bash"}]}, "deny"),
    # the steps the drain does grant are left to the rules
    ("Edit", {"file_path": f"{_HOME}/.claude/skills/publish/SKILL.md",
              "old_string": "body text", "new_string": "better body text"}, None),
    ("Edit", {"file_path": f"{_HOME}/.claude/agents/backend-developer.md",
              "old_string": "always run tests", "new_string": "always run tests first"}, None),
    ("Write", {"file_path": "docs/solutions/tooling/x.md", "content": "note"}, None),
    ("Read", {"file_path": "docs/solutions/tooling/x.md"}, None),
    ("Read", {"file_path": f"{_HOME}/.reflect/episodes/ep.md"}, None),
])
def test_guard_denies_credential_paths_and_skill_frontmatter(tool: str, tool_input: dict, decision) -> None:
    out = drain_guard.decide({"tool_name": tool, "tool_input": tool_input, "cwd": _HOME})
    assert (out or {}).get("permissionDecision") == decision, (tool, tool_input)
    if decision == "deny":
        assert out["permissionDecisionReason"].startswith("drain writer: ")


@pytest.mark.parametrize("tool,tool_input,decision", [
    # A search walks a tree, and the harness judges a read-deny rule on the
    # search root, not on the files the search reads: anything outside the
    # drain's own trees is denied here instead.
    ("Grep", {"path": _HOME, "glob": "**/.env", "pattern": "."}, "deny"),
    ("Grep", {"path": _HOME, "pattern": "BEGIN RSA PRIVATE KEY"}, "deny"),
    ("Grep", {"pattern": "jwt"}, "deny"),  # no root at all: the writer's home
    ("Glob", {"pattern": f"{_HOME}/.ssh/*"}, "deny"),
    ("Grep", {"path": "docs/solutions", "glob": "**/id_rsa", "pattern": "."}, "deny"),
    # the trees the skill's own steps search
    ("Grep", {"path": "docs/solutions", "pattern": "jwt"}, None),
    ("Grep", {"path": "docs/solutions", "pattern": "credentials"}, None),  # a word, not a path
    ("Glob", {"pattern": f"{_HOME}/.claude/skills/*/SKILL.md"}, None),
    ("Glob", {"pattern": "**/*.md", "path": "docs/solutions"}, None),
    ("Grep", {"path": f"{_HOME}/.learnings/documents", "pattern": "jwt"}, None),
])
def test_guard_scopes_where_a_search_may_run(tool: str, tool_input: dict, decision) -> None:
    out = drain_guard.decide({"tool_name": tool, "tool_input": tool_input, "cwd": _HOME})
    assert (out or {}).get("permissionDecision") == decision, (tool, tool_input)


def test_frontmatter_is_judged_by_the_file_not_by_the_text(tmp_path: Path) -> None:
    """A `---` in the body is a horizontal rule; only the file's own leading
    block is frontmatter, and only an edit anchored inside it is denied."""
    skill = tmp_path / ".claude" / "skills" / "publish" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: publish\nallowed-tools: Read\n---\n\nBody text here.\n\n---\n\nMore body.\n")
    def decide(old: str, new: str):
        out = drain_guard.decide({"tool_name": "Edit", "cwd": str(tmp_path),
                                  "tool_input": {"file_path": str(skill), "old_string": old, "new_string": new}})
        return (out or {}).get("permissionDecision")
    assert decide("allowed-tools: Read", "allowed-tools: Bash") == "deny"
    assert decide("name: publish", "name: publish") == "deny"
    assert decide("Body text here.", "Body text, revised.") is None
    assert decide("More body.", "More body, and a rule:\n\n---\n") is None


def test_the_deny_rules_and_the_guard_matcher_cover_the_same_ground() -> None:
    settings = json.loads(_flag(_lib("argv", "p"), "--settings"))
    deny = settings["permissions"]["deny"]
    assert {"Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.claude/projects/**)"} <= set(deny)
    assert all(r.startswith("Read(") for r in deny)
    matcher = settings["hooks"]["PreToolUse"][0]["matcher"]
    assert set(matcher.split("|")) >= {"Bash", "Read", "Edit", "Write", "MultiEdit", "Glob", "Grep"}


def test_extract_writer_is_structurally_tool_free() -> None:
    argv = drain_extract.writer_argv("p", model="haiku")
    assert _flag(argv, "--tools") == "" and "--strict-mcp-config" in argv
    assert _flag(argv, "--permission-mode") == "default"
    assert "--allowedTools" not in argv and "bypassPermissions" not in argv
    # Setting sources stay loaded on the extract path too: clearing them drops
    # apiKeyHelper and the env block from settings.json, so operators who
    # authenticate that way get a failing default drain (proven live).
    assert "--setting-sources" not in argv


# --------------------------------------------------------------------------- #
# the guard: decided before the call, on the normalised command
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command,decision", [
    ("reflect skill-step index docs/solutions/t/x.md docs/solutions/t/x.entities.yaml", "allow"),
    ("reflect skill-step state status", "allow"),
    ("reflect add docs/solutions/t/x.md --entities docs/solutions/t/x.entities.yaml --force", "allow"),
    ('reflect search "jwt expiry"', "allow"),
    ("cd ~ && reflect skill-step metrics --accepted 1", "allow"),  # a leading cd home
    ("reflect skill-step metrics \\\n    --accepted 1 --agents \"a,b\"", "allow"),  # line continuation
    ("reflect skill-step revise --source \"/t.jsonl\" --actions '[{\"reason\":\"$5 `x`\"}]'", "allow"),
    # bypasses: each of these used to be allowed
    ("reflect add docs/solutions/x.md\nrm -rf ~", "deny"),  # a second line
    ("reflect add `id`", "deny"),  # command substitution
    ('reflect add "$(id)"', "deny"),
    ("reflect search $HOME", "deny"),  # expansion
    ("env FOO=1 reflect add docs/solutions/x.md", "deny"),  # env prefixes
    ("PYTHONPATH=docs/solutions reflect search x", "deny"),
    ("REFLECT_SKILL_SCRIPTS_DIR=docs/solutions/x reflect skill-step metrics", "deny"),
    ("exec reflect search x", "deny"),
    ("cd /tmp && reflect search x", "deny"),  # cd anywhere but home or cwd
    ("reflect add ~/.ssh/id_rsa", "deny"),  # outside docs/solutions
    ("reflect add docs/solutions/{x.md,../../.ssh/id_rsa}", "deny"),  # brace expansion
    ("reflect add docs/solutions/../../.ssh/id_*", "deny"),  # glob
    ("reflect add docs/solutions/x.md ~root/.ssh/id_rsa", "deny"),  # tilde
    ("reflect add docs/solutions/../../.ssh/id_rsa --force", "deny"),
    ("reflect add docs/solutions/x.md --entities /etc/passwd", "deny"),
    ("reflect skill-step index /etc/passwd docs/solutions/x.entities.yaml", "deny"),
    ("reflect search jwt | head -3", "deny"),  # a pipe
    ("cd /x; reflect add x", "deny"),  # a second command
    ("reflect add x > out.txt", "deny"),  # a redirect
    ("python3 /somewhere/scripts/reflect_index.py a b", "deny"),  # no python3 at all
    ("python3 -u /somewhere/scripts/validate_sidecar.py --strict a", "deny"),
    ("uv run reflect add x", "deny"),
    ("git commit -m 'reflect: add learning'", "deny"),
    ("curl -s https://example.com", "deny"),
    ("reflect", "deny"),
    ("", "deny"),
])
def test_guard_decides_bash_on_the_normalised_command(command: str, decision: str) -> None:
    proc = subprocess.run([sys.executable, str(_GUARD)],
                          input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == decision, command
    assert out["permissionDecisionReason"].startswith("drain writer: ")
    if decision == "deny":
        assert "reflect skill-step" in out["permissionDecisionReason"]


def test_guard_leaves_other_tools_and_bad_input_to_the_rules() -> None:
    for payload in (json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x"}}), "not json", ""):
        proc = subprocess.run([sys.executable, str(_GUARD)], input=payload, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0 and proc.stdout == "", payload


# --------------------------------------------------------------------------- #
# SKILL.md steps versus the rules, both layouts
# --------------------------------------------------------------------------- #

def _skill_calls(skill_text: str, home: str) -> list[tuple[str, str, dict, str | None]]:
    """Every tool call the skill's steps name, as (label, tool, tool_input,
    excluded_key). excluded_key names the _WRITER_EXCLUDED line that must
    cover the call when no rule does."""
    calls = []
    # A command continues over lines ending in a backslash, as the model
    # would run it (shlex joins the continuation the way bash does).
    command_re = r"((?:[^\n\\]|\\[^\n])*(?:\\\n(?:[^\n\\]|\\[^\n])*)*)"
    for m in re.finditer(r"^\s*(python3? \S+\.py" + command_re + ")", skill_text, re.MULTILINE):
        calls.append((m.group(1).strip(), "Bash", {"command": m.group(1).strip()}, "python3"))
    for m in re.finditer(r"^\s*(?:reflect (?:on|off)\s+->\s+)?(reflect (?:add|search|skill-step) " + command_re + ")",
                         skill_text, re.MULTILINE):
        calls.append((m.group(1).strip().splitlines()[0], "Bash", {"command": m.group(1).strip()}, None))
    if "for skill_md in" in skill_text:
        calls.append(("step 2.5 shell loop", "Bash",
                      {"command": "for skill_md in ~/.claude/skills/*/SKILL.md; do :; done"}, "step 2.5 shell loop"))
    if "Commit with descriptive message" in skill_text:
        calls.append(("step 6.8 git commit", "Bash", {"command": "git commit -m x"}, "step 6.8 git commit"))
    calls += [
        ("step 6.1 agent edit", "Edit", {"file_path": f"{home}/.claude/agents/backend-developer.md"}, None),
        ("step 2.5 skill edit", "Edit", {"file_path": f"{home}/.claude/skills/publish/SKILL.md"}, None),
        ("step 2 CLAUDE.md edit", "Edit", {"file_path": f"{home}/.claude/CLAUDE.md"}, "~/.claude/CLAUDE.md"),
        ("step 3 new skill", "Write", {"file_path": f"{home}/.claude/skills/new-skill/SKILL.md"}, "new skill creation"),
        ("memory file", "Edit", {"file_path": ".agents/MEMORY.md"}, "memory files"),
        ("step 6.2 note", "Write", {"file_path": "docs/solutions/tooling/x.md"}, None),
        ("step 6.3 sidecar", "Write", {"file_path": "docs/solutions/tooling/x.entities.yaml"}, None),
        ("step 7 episode", "Write", {"file_path": f"{home}/.reflect/episodes/ep-2026-01-01-abcd.md"}, None),
    ]
    return calls


def _scope_matches(scope: str, path: str, cwd: str, home: str) -> bool:
    """Claude Code's Write(...)/Edit(...) scope: ~ is the home, a relative
    scope is under the cwd, ** spans directories."""
    scope = scope.replace("~", home, 1) if scope.startswith("~") else scope
    if not scope.startswith("/"):
        scope = f"{cwd}/{scope}"
    path = path if path.startswith("/") else f"{cwd}/{path}"
    pattern = re.escape(scope).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(pattern, path) is not None


def _rule_count(rules: list[str], tool: str, tool_input: dict, cwd: str, home: str) -> int:
    """How many allow rules (as spelled) cover the call; the guard is separate."""
    n = 0
    for rule in rules:
        if rule == tool:
            n += 1
            continue
        m = re.fullmatch(r"(\w+)\((.*)\)", rule)
        if not m or m.group(1) != tool:
            continue
        inner = m.group(2)
        if tool == "Bash":
            prefix = inner[:-2] if inner.endswith(":*") else inner
            cmd = tool_input["command"]
            if cmd == prefix or cmd.startswith(prefix + " "):
                n += 1
        elif _scope_matches(inner, tool_input["file_path"], cwd, home):
            n += 1
    return n


def _assert_skill_and_rules_agree(skill_text: str, rules: list[str], excluded: str, home: str, layout: str) -> None:
    problems = []
    for label, tool, tool_input, excluded_key in _skill_calls(skill_text, home):
        n = _rule_count(rules, tool, tool_input, cwd=home, home=home)
        deliberate = excluded_key is not None and excluded_key in excluded
        if tool == "Bash":
            # The guard decides Bash: a granted command is allowed by both the
            # guard and exactly one rule; an excluded one by neither.
            # The model fills the skill's {placeholders} before it runs the command.
            filled = {"command": re.sub(r"\{(\w+)\}", r"\1", tool_input["command"])}
            guard = drain_guard.decide({"tool_name": "Bash", "tool_input": filled})["permissionDecision"]
            if (n == 1 and guard == "allow" and not deliberate) or (n == 0 and guard == "deny" and deliberate):
                continue
            problems.append(f"{label}: matched {n} rule(s), guard={guard}, deliberately excluded={deliberate}")
            continue
        if (n == 1 and not deliberate) or (n == 0 and deliberate):
            continue
        problems.append(f"{label}: matched {n} rule(s), deliberately excluded={deliberate}")
    assert not problems, f"{layout}: SKILL.md steps and the allow rules disagree:\n  " + "\n  ".join(problems)


def test_skill_names_no_script_path_and_every_step_is_a_skill_step_command() -> None:
    text = _SKILL.read_text()
    assert "{{HOME_TOOL_DIR}}/skills/reflect/scripts" not in text
    assert not re.search(r"^\s*python3? ", text, re.MULTILINE), "a python step survived"
    steps = set(re.findall(r"reflect skill-step (\S+)", text))
    assert steps == {"index", "metrics", "state", "revise", "observe"}, steps


def test_plugin_runtime_layout_rules_cover_the_skill_steps_exactly_once(tmp_path: Path) -> None:
    """Under the plugin runtime SKILL.md is served raw; with no path in any
    step the raw text and the rules agree without a resolution step."""
    home = str(tmp_path)
    assert "reflect skill-step index <note> <sidecar>" in _lib("prompt", "")[0]
    _assert_skill_and_rules_agree(_SKILL.read_text(), _lib("rules"), _lib("excluded")[0], home, "plugin runtime")


def test_adapter_layout_rendered_skill_and_installed_rules_agree(tmp_path: Path) -> None:
    """An adapter install renders SKILL.md and ships the lib next to the
    scripts; the rendered text names no script path and the installed guard
    exists where the installed settings document says."""
    home = tmp_path / "home"
    home.mkdir()
    adapter = _PLUGIN / "adapters" / "claude" / "claude_adapter.py"
    env = {**os.environ, "HOME": str(home), "REFLECT_DISABLED": "1"}
    subprocess.run([sys.executable, str(adapter), "install", "--home", str(home)], env=env, check=True,
                   capture_output=True, timeout=120)
    installed_lib = home / ".claude" / "skills" / "reflect" / "hooks" / "lib" / "writer_argv.sh"
    rendered = (home / ".claude" / "skills" / "reflect" / "SKILL.md").read_text()
    assert "{{HOME_TOOL_DIR}}" not in rendered
    assert not re.search(r"python3? \S+/scripts/\w+\.py", rendered)
    rules = _lib("rules", lib=installed_lib)
    assert rules == _lib("rules"), "the installed rules differ from the checkout's (they carry no path)"
    guard = Path(_lib("guard", lib=installed_lib)[0])
    assert guard.is_file() and guard.is_relative_to(home / ".claude" / "skills" / "reflect" / "scripts")
    _assert_skill_and_rules_agree(rendered, rules, _lib("excluded", lib=installed_lib)[0], str(home), "adapter")


# --------------------------------------------------------------------------- #
# what a run does with denials, the receipt and the binary (stub claude, real hook)
# --------------------------------------------------------------------------- #

def _run_drain(tmp_path: Path, envelope: dict, *, stub_body: str = "", env_extra: dict | None = None,
               path: str | None = None) -> tuple[Path, Path, Path]:
    from test_drain_writer_default import _big_transcript

    transcript = _big_transcript(tmp_path / "w.jsonl")
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("#!/usr/bin/env bash\n" 'printf "%s\\0" "$@" > "$REFLECT_TEST_ARGV"\n'
                    'printf "%s" "${REFLECT_NESTED:-}" > "$REFLECT_TEST_NESTED"\n'
                    + stub_body + f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n")
    stub.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    queue = state / "pending_reflections.jsonl"
    queue.write_text(json.dumps({
        "session_id": "w", "transcript_path": str(transcript), "trigger": "stop",
        "cwd": "/", "scope": "session", "harness": "claude", "ts": "2026-08-11T10:01:00Z",
    }) + "\n")
    env = {**os.environ, "REFLECT_STATE_DIR": str(state), "REFLECT_DRAIN_DRY_RUN": "0",
           "REFLECT_DRAIN_CLAUDE_BIN": str(stub), "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
           "REFLECT_DRAIN_CASCADE": "0", "REFLECT_QUOTA_GATE": "0", "REFLECT_DRAIN_SKIP_REINDEX": "1",
           "REFLECT_QUIET_INSTALL_WARNING": "1", "REFLECT_DRAIN_MAX": "1", "REFLECT_DRAIN_WRITER": "agentic",
           "REFLECT_DRAIN_CWD": str(cwd), "REFLECT_TEST_ARGV": str(tmp_path / "argv.bin"),
           "REFLECT_TEST_NESTED": str(tmp_path / "nested.txt"),
           "GLOBAL_LEARNINGS_PATH": str(tmp_path / "kb"), **(env_extra or {})}
    env.pop("REFLECT_NESTED", None)
    if path is not None:
        env["PATH"] = path
    subprocess.run(["bash", str(_DRAIN)], env=env, capture_output=True, text=True, timeout=180, check=False)
    return state, queue, transcript


def _ledger(state: Path) -> list[dict]:
    return [json.loads(line) for line in (state / "drain-cost.jsonl").read_text().splitlines() if line.strip()]


_USAGE = {"input_tokens": 900, "output_tokens": 40}
_OK = {"type": "result", "subtype": "success", "is_error": False, "result": "captured one learning",
       "num_turns": 3, "total_cost_usd": 0.02, "usage": _USAGE}


def test_denials_are_logged_and_ledgered_and_never_fail_the_run(tmp_path: Path) -> None:
    """git commit (step 6.8) and a python spelling are not granted (the guard
    denied them before they ran): logged with what was asked, ledgered with
    the real tokens, outcome still decided by the run itself."""
    envelope = {**_OK, "permission_denials": [
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "git commit -m 'reflect: add learning'"}},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": "python3 /x/scripts/reflect_index.py a b"}},
    ]}
    state, queue, transcript = _run_drain(tmp_path, envelope)
    log = (state / "drain.log").read_text()
    assert "DENIED Bash: git commit" in log and "DENIED Bash: python3 /x/scripts/reflect_index.py" in log, log
    assert "    OK turns=3" in log and "denials=2" in log
    assert "MISCONFIGURED" not in log and "RETRYABLE" not in log
    assert not queue.exists() or str(transcript) not in queue.read_text(), "a clean run was requeued"
    row = [r for r in _ledger(state) if r["outcome"] == "ok"][-1]
    assert row["denials"] == 2 and row["tokens"] == 940 and row["cost_usd"] == 0.02
    argv = (tmp_path / "argv.bin").read_bytes().split(b"\0")[:-1]
    assert b"--setting-sources" not in argv
    assert argv[0] == b"-p" and b"Headless drain rules" in argv[1]  # the addendum reached the writer
    # The writer is a nested claude: its reflect hooks exit at once.
    assert (tmp_path / "nested.txt").read_text() == "1"


def test_receipt_decides_notes_landed_and_indexed(tmp_path: Path) -> None:
    """The writer's `reflect skill-step index` appends one line per indexed
    note to the receipt the hook exported; the ledger's notes_landed and
    indexed come from it, not from mtimes under a shared docs tree."""
    (tmp_path / "cwd" / "docs" / "solutions" / "t").mkdir(parents=True)
    (tmp_path / "cwd" / "docs" / "solutions" / "t" / "other-session.md").write_text("# not this run\n")
    body = ('[ -n "$REFLECT_DRAIN_RECEIPT" ] || exit 9\n'
            'printf \'{"note": "/cwd/docs/solutions/t/a.md", "doc_id": "lrn-a-1"}\\n\' >> "$REFLECT_DRAIN_RECEIPT"\n'
            'printf \'{"note": "/cwd/docs/solutions/t/a.md", "doc_id": "lrn-a-1"}\\n\' >> "$REFLECT_DRAIN_RECEIPT"\n'
            'printf \'{"note": "/cwd/docs/solutions/t/b.md", "doc_id": ""}\\n\' >> "$REFLECT_DRAIN_RECEIPT"\n')
    state, _, _ = _run_drain(tmp_path, _OK, stub_body=body)
    log = (state / "drain.log").read_text()
    assert "    OK turns=3" in log and "notes_landed=2 indexed=2" in log, log
    row = [r for r in _ledger(state) if r["outcome"] == "ok"][-1]
    assert row["notes_landed"] == 2 and row["indexed"] == 2
    assert not list(Path(os.environ.get("TMPDIR", "/tmp")).glob("reflect-receipt.*")), "receipt left behind"


def test_reflect_binary_off_path_reaches_the_writer(tmp_path: Path) -> None:
    """A `uv tool install` layout keeps reflect in a directory the hook's PATH
    lacks; the hook exports REFLECT_BIN and puts its directory first on the
    writer's PATH, so `reflect skill-step` in the child resolves."""
    reflect = tmp_path / "tools" / "bin" / "reflect"
    reflect.parent.mkdir(parents=True)
    reflect.write_text("#!/usr/bin/env bash\necho stub-reflect\n")
    reflect.chmod(0o755)
    body = ('printf "%s\\n%s\\n%s" "$(command -v reflect)" "$REFLECT_BIN" "$REFLECT_SKILL_SCRIPTS_DIR" > "$REFLECT_TEST_ARGV.reflect"\n')
    bare_path = "/usr/bin:/bin:/usr/sbin:/sbin:" + os.path.dirname(sys.executable)
    _run_drain(tmp_path, _OK, stub_body=body, env_extra={"REFLECT_DRAIN_REFLECT_BIN": str(reflect)}, path=bare_path)
    found, exported, scripts = (tmp_path / "argv.bin.reflect").read_text().splitlines()
    assert Path(found).resolve() == reflect.resolve(), found
    assert exported == str(reflect)
    assert Path(scripts) == (_PLUGIN / "scripts").resolve()
