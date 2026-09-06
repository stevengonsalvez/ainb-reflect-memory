"""Gate 1: clean-home install matrix against the merge-base baseline.

For each adapter, install into a throwaway HOME from the baseline checkout and
from this checkout, and diff the installed tree, hook commands, hook target
paths and surviving placeholders. Any difference must be whitelisted below
with the reason. Then, for this checkout: no placeholder survives, every hook
command path exists, and every absolute path the drain's permission rules
name exists in that harness's layout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import (
    HARNESS_DIR,
    HARNESSES,
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

# --------------------------------------------------------------------------- #
# Whitelist: the only review surface for an intended install change. Each
# predicate names the file(s) and the reason. Empty means "identical to main".
# --------------------------------------------------------------------------- #

def _writer_argv_library(key: str, old, new) -> bool:
    """This branch moves the writer argv into hooks/lib/writer_argv.sh (new
    file) and makes the hook source it; drain_extract gains writer_argv."""
    return (
        key.startswith("tree.skills/reflect/hooks/lib/writer_argv.sh.")
        or key == "tree.skills/reflect/hooks/reflect-drain-bg.sh.sha"
        or key == "tree.skills/reflect/scripts/drain_extract.py.sha"
    )


def _rendered_placeholder(key: str, old, new) -> bool:
    """Adapters now render {{HOME_TOOL_DIR}} in every SKILL.md they write."""
    if key.startswith("tree.skills/") and key.endswith("/SKILL.md.sha"):
        return True
    return key.startswith("placeholders") and new in ("<absent>", [])


def _claude_installs_runtime_files(key: str, old, new) -> bool:
    """The Claude adapter now syncs skills/*/{hooks,scripts,assets,references}
    and the plugin-root resources, so its hook command targets exist."""
    if key.startswith("tree.skills/") and old == "<absent>":
        return True
    return key.startswith("hook_paths.") and old is False and new is True


ALLOWED_INSTALL_DIFF = {
    "claude": any_of(_rendered_placeholder, _claude_installs_runtime_files),
    "codex": any_of(_writer_argv_library, _rendered_placeholder),
    "copilot": any_of(_writer_argv_library, _rendered_placeholder),
}


@pytest.mark.parametrize("harness", HARNESSES)
def test_install_matches_baseline(harness: str, captures) -> None:
    baseline, branch = captures(f"install-{harness}")
    assert_same_as_baseline(f"install-{harness}", baseline, branch, allowed=ALLOWED_INSTALL_DIFF[harness])


@pytest.mark.parametrize("harness", HARNESSES)
def test_no_placeholder_survives_install(harness: str, captures) -> None:
    _, branch = captures(f"install-{harness}")
    assert branch["placeholders"] == [], branch["placeholders"]


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_hook_command_path_exists(harness: str, captures) -> None:
    """A hook command that names a file under HOME must find it there."""
    _, branch = captures(f"install-{harness}")
    missing = sorted(p for p, exists in branch["hook_paths"].items() if not exists)
    assert not missing, f"{harness}: hook commands target missing files: {missing}"


def _installed_lib(harness: str, harness_dir: Path) -> Path:
    """Where the argv library lives for this harness: the plugin runtime cache
    (this checkout's plugin/) for Claude, the deployed copy for the others."""
    if harness == "claude":
        return WRITER_ARGV_LIB
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
