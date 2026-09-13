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
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
        """A SKILL.md may change only into render(baseline text) for this layout."""
        split = _split(key)
        if not split or split[1] != "text" or not isinstance(old, str) or not isinstance(new, str):
            return False
        dst = Path(f"{harness_dir}/{split[0]}")
        return new == render_for_layout(old, dst)

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


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_drain_permission_path_exists_in_installed_layout(harness: str, home: Path) -> None:
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
    rules = permission_rules(argv)
    missing = sorted(p for p in absolute_paths_in_rules(rules) if not Path(p).exists())
    assert not missing, (
        f"{harness}: drain permission rules name paths missing from the installed layout: "
        f"{missing}\nrules: {rules}"
    )
    for settings in flag_values(argv, "--settings"):
        if settings.startswith("/"):
            assert Path(settings).exists(), f"--settings file missing: {settings}"


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
