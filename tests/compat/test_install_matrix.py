"""Gate 1: clean-home install matrix against the merge-base baseline.

For each adapter (claude, codex, copilot, hermes), install into a throwaway
HOME from the baseline checkout and from this checkout, and diff the
installed tree, hook commands, hook target paths and unresolved markers.
Every difference must fall into a whitelist bucket that PROVES the intended
transform rather than exempting a path: a changed SKILL.md must equal
render(baseline text); a new or changed file must be byte-identical to the
plugin source it mirrors; a hook target path may only go from missing to
present; unresolved markers may only disappear. Then, for this checkout: no
marker survives, every hook command path exists, every path the drain's
permission rules name exists, and every script a rendered skill names exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ._drain_skill_rewrite import drain_skill_rewrite
from .conftest import (
    HARNESS_DIR,
    HARNESSES,
    PLUGIN,
    REPO,
    WRITER_ARGV_LIB,
    absolute_paths_in_rules,
    agentic_writer_argv,
    any_of,
    assert_same_as_baseline,
    clean_env,
    flag_values,
    install_adapter,
    permission_rules,
)

sys.path.insert(0, str(PLUGIN / "adapters"))
from base import UNRESOLVED_MARKER, render_for_layout

from .capture import _TS_RE

_KEY_RE = re.compile(r"^tree\.(?P<rel>.+)\.(?P<field>text|sha|exec)$")


def _split(key: str) -> tuple[str, str] | None:
    m = _KEY_RE.match(key)
    return (m.group("rel"), m.group("field")) if m else None


def _source_for(harness: str, rel: str) -> Path | None:
    """The plugin source an installed file mirrors, or None if it mirrors nothing."""
    parts = Path(rel).parts
    if len(parts) >= 3 and parts[0] == "skills":
        name, rest = parts[1], Path(*parts[2:])
        per_skill = PLUGIN / "skills" / name / rest
        if per_skill.exists():
            return per_skill
        if name == "reflect":
            if rest.parts[0] == "shim":  # hermes deploys its own shim dir
                return PLUGIN / "adapters" / "hermes" / rest
            umbrella = PLUGIN / rest  # plugin-root resources + reflect.toml
            if umbrella.exists():
                return umbrella
    return None


def _source_sha(path: Path) -> str:
    """Same digest capture.py records: normalized text (timestamps masked)."""
    raw = path.read_bytes()
    try:
        return hashlib.sha256(_TS_RE.sub("<TS>", raw.decode("utf-8")).encode()).hexdigest()[:16]
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()[:16]


# Item-level review surface: plugin source files a PR is allowed to change
# in the installed layout, each with the reason. Anything else that differs
# from the baseline install is a diff, even if it matches the branch's own
# source (that comparison would be a tautology: the adapter copied it).
ALLOWED_PLUGIN_CHANGES: dict[str, str] = {
    "hooks/reflect-drain-bg.sh": "gate PR: sources hooks/lib/writer_argv.sh with an explicit check",
    "hooks/lib/writer_argv.sh": "gate PR: side-effect-free writer argv library, new file",
    "adapters/hermes/hermes_adapter.py": "gate PR: hermes renders and syncs plugin-root resources",
    "scripts/drain_extract.py": "gate PR: writer_argv extracted so the gate reads the exact argv the extract writer runs",
    # #38 capture redaction
    "scripts/secret_redact.py": "#38: stdlib copy of the engine's secret tables for the plugin's own scripts",
    "scripts/reflect_db.py": "#38: add_learning redacts the title and quote at the reflect.db boundary",
    "scripts/kb_export.py": "#38: the export re-redacts every text cell as defence in depth",
    "scripts/skill_index.py": "#38: the skill summary is redacted before it is stored",
    # #39 broker route
    "assets/learning_template.md": "#39: top-level repo, commit and source_path keys a pin is built from",
    # #40 drain allowlist
    "scripts/drain_guard.py": "#40: PreToolUse guard the writer's settings document installs, new file",
    "scripts/drain_extract.py": "#40: shared no-tools flags, the nested marker and the receipt line",
    "scripts/reflect_cascade.py": "#40: the cascade fails closed without secret_redact.py",
    "scripts/reflect_synthesis.py": "#40: the synthesis child is tool-free and nested",
    "skills/recall/hooks/session_start_recall.py": "#40: exits at once under REFLECT_NESTED",
    "skills/recall/hooks/user_prompt_submit_recall.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/idle_reflect.sh": "#40: exits at once under REFLECT_NESTED",
    "hooks/reflect-maintenance-watch.sh": "#40: exits at once under REFLECT_NESTED",
    "hooks/README.md": "#40: the guard, the receipt and the nested marker rows",
    "docs/architecture.md": "#40: the reworked drain surface",
    "hooks/error_occurred_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/notification_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/permission_request_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/postcompact_bookkeeping.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/posttooluse_minilearning.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/posttoolusefailure_minilearning.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/precompact_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/pretooluse_context.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/session_end_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/stop_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/subagent_start_recall.py": "#40: exits at once under REFLECT_NESTED",
    "hooks/subagent_stop_reflect.py": "#40: exits at once under REFLECT_NESTED",
    "skills/recall/scripts/recall.py": "#39: the transcript path from source_transcript; #40: HyDE keeps setting sources, nested",
}


def _rendered_source_sha(source: Path, dst: Path) -> str:
    """The digest capture.py records for a rendered copy of ``source`` at ``dst``."""
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()[:16]
    return hashlib.sha256(_TS_RE.sub("<TS>", render_for_layout(text, dst)).encode()).hexdigest()[:16]


def _make_whitelist(harness: str, baseline_tree: Path):
    harness_dir = f"$HOME/{HARNESS_DIR[harness]}"

    def rendered_skill(key: str, old, new) -> bool:
        """A SKILL.md may change only into render(baseline text) for this layout,
        or, for the reflect skill, render(drain_skill_rewrite(baseline text)):
        the drain fix turned the index block into two literal commands and the
        metrics/state lines into python3 (tests/compat/_drain_skill_rewrite.py)."""
        split = _split(key)
        if not split or split[1] != "text" or not isinstance(old, str) or not isinstance(new, str):
            return False
        dst = Path(f"{harness_dir}/{split[0]}")
        return new in (render_for_layout(old, dst), render_for_layout(drain_skill_rewrite(old), dst))

    def mirrors_unchanged_source(key: str, old, new) -> bool:
        """A new or changed non-skill file must equal its plugin source AS IT
        IS ON THE BASELINE (byte-identical, or rendered for this layout), so
        the proof is that the adapter installs what main already had. A source
        the PR changed must be named in ALLOWED_PLUGIN_CHANGES instead."""
        split = _split(key)
        if not split or split[1] == "text":
            return False
        rel, field = split
        source = _source_for(harness, rel)
        if source is None or not source.exists():
            return False
        source_rel = source.relative_to(PLUGIN)
        baseline_source = baseline_tree / "plugin" / source_rel
        if str(source_rel) in ALLOWED_PLUGIN_CHANGES:
            reference = source  # the PR names this file and its reason
        elif baseline_source.exists() and baseline_source.read_bytes() == source.read_bytes():
            reference = baseline_source
        else:
            return False  # changed on the branch and not whitelisted
        if field == "sha":
            dst = Path(f"{harness_dir}/{rel}")
            return new in (_source_sha(reference), _rendered_source_sha(reference, dst))
        return new == os.access(reference, os.X_OK)

    def hook_target_now_exists(key: str, old, new) -> bool:
        return key.startswith("hook_paths.") and old is False and new is True

    def marker_rendered_away(key: str, old, new) -> bool:
        if key == "unresolved":  # the whole map collapsed to empty
            return new == {}
        return key.startswith("unresolved.") and new == "<absent>"

    return any_of(rendered_skill, mirrors_unchanged_source, hook_target_now_exists, marker_rendered_away)


@pytest.mark.parametrize("harness", HARNESSES)
def test_install_matches_baseline(harness: str, captures, baseline_tree: Path) -> None:
    baseline, branch = captures(f"install-{harness}")
    assert_same_as_baseline(f"install-{harness}", baseline, branch,
                            allowed=_make_whitelist(harness, baseline_tree))


@pytest.mark.parametrize("harness", HARNESSES)
def test_no_marker_survives_install(harness: str, captures) -> None:
    _, branch = captures(f"install-{harness}")
    assert branch["unresolved"] == {}, branch["unresolved"]


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_hook_command_path_exists(harness: str, captures) -> None:
    """A hook command that names a file under HOME must find it there."""
    _, branch = captures(f"install-{harness}")
    missing = sorted(p for p, exists in branch["hook_paths"].items() if not exists)
    assert not missing, f"{harness}: hook commands target missing files: {missing}"


_SCRIPT_RE = re.compile(r"(?:python3?|uv run)\s+\"?(/[^\s\"]+\.py)\"?")


@pytest.mark.parametrize("harness", HARNESSES)
def test_rendered_skill_paths_exist(harness: str, home: Path) -> None:
    """Every script or resource a rendered SKILL.md names by absolute path exists."""
    harness_dir = install_adapter(harness, home)
    missing = []
    for skill_md in sorted((harness_dir / "skills").glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        assert not UNRESOLVED_MARKER.search(text), skill_md
        for m in _SCRIPT_RE.finditer(text):
            if not Path(m.group(1)).exists():
                missing.append(f"{skill_md.name}: {m.group(1)}")
        for m in re.finditer(re.escape(str(harness_dir)) + r"/skills/[^\s`'\")]+", text):
            if not Path(m.group(0)).exists():
                missing.append(f"{skill_md.parent.name}/{skill_md.name}: {m.group(0)}")
    assert not missing, f"{harness}: rendered skill names missing paths:\n  " + "\n  ".join(sorted(set(missing)))


def _installed_lib(harness: str, harness_dir: Path) -> Path:
    """The deployed copy of the argv library in this harness's layout. Every
    adapter installs it (the claude adapter too), so the assertion is about
    the installed file, never this checkout's own copy."""
    return harness_dir / "skills" / "reflect" / "hooks" / "lib" / "writer_argv.sh"


def _lib_value(lib: Path, fn: str, env: dict[str, str]) -> str:
    proc = subprocess.run(["bash", "-c", f'source "$1" && {fn}', "x", str(lib)], env=env,
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _guard_and_rules(lib: Path, env: dict[str, str]) -> tuple[list[str], Path, dict]:
    argv = agentic_writer_argv("compat probe", env, lib)
    settings = json.loads(flag_values(argv, "--settings")[0])
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith("python3 /"), command
    return permission_rules(argv), Path(command.split(" ", 1)[1]), settings


@pytest.mark.parametrize("harness", HARNESSES)
def test_installed_rules_carry_no_path_and_the_guard_exists(harness: str, home: Path) -> None:
    harness_dir = install_adapter(harness, home)
    lib = _installed_lib(harness, harness_dir)
    assert lib.exists(), f"writer argv library missing from the {harness} layout: {lib}"
    argv = agentic_writer_argv("compat probe", clean_env(home), lib)
    # The argv shape the hook promises, asserted explicitly.
    assert Path(argv[0]).name == "claude"
    assert argv[1] == "-p" and argv[2] == "compat probe"
    assert flag_values(argv, "--output-format") == ["json"]
    assert len(flag_values(argv, "--model")) == 1
    assert len(flag_values(argv, "--max-turns")) == 1
    rules, guard, _ = _guard_and_rules(lib, clean_env(home))
    # One command surface: the rules name no path, so no layout can make the
    # rule and the command the skill spells differ.
    assert absolute_paths_in_rules(rules) == set(), rules
    assert {r for r in rules if r.startswith("Bash(")} == {
        "Bash(reflect skill-step:*)", "Bash(reflect add:*)", "Bash(reflect search:*)"}
    assert guard.is_file() and guard.name == "drain_guard.py", guard
    assert guard.is_relative_to(harness_dir / "skills" / "reflect" / "scripts")
    skill = (harness_dir / "skills" / "reflect" / "SKILL.md").read_text(encoding="utf-8")
    assert not re.search(r"python3? \S+/scripts/\w+\.py", skill), "the rendered skill names a script path"
    assert "reflect skill-step index docs/solutions/" in skill


def test_symlinked_home_renders_and_rules_the_same(tmp_path: Path) -> None:
    """A symlinked $HOME (or a symlinked ~/.claude, macOS /var versus
    /private/var): the physical and the logical spelling of the same
    directory. The rules carry no path, so they are identical under both;
    the guard the settings document names exists under both spellings; and
    the rendered SKILL.md names no directory that could differ."""
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "home-link"
    link.symlink_to(real, target_is_directory=True)
    harness_dir = install_adapter("claude", link)
    lib = _installed_lib("claude", harness_dir)
    rules_link, guard_link, settings_link = _guard_and_rules(lib, clean_env(link))
    rules_real, guard_real, settings_real = _guard_and_rules(real / ".claude" / "skills" / "reflect" / "hooks" / "lib" / "writer_argv.sh", clean_env(real))
    assert rules_link == rules_real and absolute_paths_in_rules(rules_link) == set()
    assert settings_link["permissions"] == settings_real["permissions"]
    assert guard_link.is_file() and guard_real.is_file()
    assert guard_link.resolve() == guard_real.resolve()
    skill = (link / ".claude" / "skills" / "reflect" / "SKILL.md").read_text(encoding="utf-8")
    assert not re.search(r"(?:python3?|uv run)\s+\S+", skill), "the rendered skill names a script by path"
    # Resources it names under the home resolve under both spellings.
    for spelling in (link, real):
        for m in re.finditer(re.escape(str(spelling)) + r"/[^\s`'\")]+", skill):
            assert Path(m.group(0)).exists(), m.group(0)


def test_plugin_runtime_addendum_and_guard_match_the_checkout(home: Path) -> None:
    """Under the plugin runtime SKILL.md is served raw; it names no script
    path, the addendum states the one command surface, and the guard the
    settings document names is this checkout's."""
    env = clean_env(home)
    addendum = _lib_value(WRITER_ARGV_LIB, 'drain_writer_prompt ""', env)
    assert "reflect skill-step index <note> <sidecar>" in addendum
    rules, guard, _ = _guard_and_rules(WRITER_ARGV_LIB, env)
    assert absolute_paths_in_rules(rules) == set() and guard == (PLUGIN / "scripts" / "drain_guard.py").resolve()
    raw = (PLUGIN / "skills" / "reflect" / "SKILL.md").read_text(encoding="utf-8")
    assert "{{HOME_TOOL_DIR}}/skills/reflect/scripts" not in raw
    assert "reflect skill-step index docs/solutions/" in raw


def test_plugin_manifest_asset_contract_holds() -> None:
    """Every ${CLAUDE_PLUGIN_ROOT} path in the manifests exists (the marketplace
    plugin layout Stevie's install uses)."""
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_plugin_contract.py")],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_harness_dirs_do_not_cross_contaminate(home: Path) -> None:
    for harness in HARNESSES:
        install_adapter(harness, home)
    for harness in HARNESSES:
        assert (home / HARNESS_DIR[harness]).is_dir()
    assert not (home / ".claude" / "hooks.json").exists()
    assert not (home / ".codex" / "settings.json").exists()
