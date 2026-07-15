"""Manifest parity tests for plugin skill registration.

Regression cover for the 11-day silent outage: a1cc2ba dropped the ``skills``
array from the root manifest on the theory that skills auto-discover, but
Claude Code only auto-discovers them at ``<plugin-root>/skills/`` and reflect
keeps its skills at ``<plugin-root>/plugin/skills/``. The result was a plugin
that loaded its 13 hooks fine and registered Skills (0), so every
``claude -p "/reflect ..."`` the drain issued came back "Unknown command",
exited 0, and was logged as success.

Nothing failed, so nothing caught it. These tests fail instead.
"""

import json
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]   # plugin/
_REPO_ROOT = _PLUGIN_ROOT.parent
_ROOT_MANIFEST = _REPO_ROOT / ".claude-plugin" / "plugin.json"
_SKILLS_DIR = _PLUGIN_ROOT / "skills"


def _manifest() -> dict:
    return json.loads(_ROOT_MANIFEST.read_text())


def _skill_dirs() -> list[Path]:
    return sorted(d for d in _SKILLS_DIR.iterdir() if (d / "SKILL.md").is_file())


def test_root_manifest_declares_skills():
    """The array must exist. Without it the loader registers zero skills."""
    skills = _manifest().get("skills")
    assert skills, (
        "root .claude-plugin/plugin.json has no 'skills' array. Skills live in "
        "plugin/skills/, NOT <root>/skills/, so they do not auto-discover: the "
        "plugin will register Skills (0) and every /reflect command will be "
        "'Unknown command' while hooks keep working."
    )


def test_every_skill_dir_is_declared():
    """A new skill dir that nobody adds to the array is a skill nobody can run."""
    declared = {s.strip("./").rstrip("/") for s in _manifest()["skills"]}
    on_disk = {f"plugin/skills/{d.name}" for d in _skill_dirs()}
    missing = on_disk - declared
    assert not missing, f"skill dirs present but not declared in plugin.json: {sorted(missing)}"


def test_every_declared_path_exists():
    """A declared path that moved or was renamed silently drops that skill."""
    for entry in _manifest()["skills"]:
        path = _REPO_ROOT / entry.strip("./").rstrip("/")
        assert path.is_dir(), f"declared skill path does not exist: {entry}"
        assert (path / "SKILL.md").is_file(), f"declared skill path has no SKILL.md: {entry}"


def _all_manifests() -> list[Path]:
    return sorted(p for p in _REPO_ROOT.rglob("plugin.json")
                  if "node_modules" not in p.parts and ".git" not in p.parts)


def test_all_manifests_agree_on_version():
    """Four manifests carry a version; a release that bumps some strands the rest.

    The 5.2.1 release originally shipped with .codex-plugin/plugin.json and
    plugin/plugin.json still reading 5.2.0.
    """
    versions = {str(p.relative_to(_REPO_ROOT)): json.loads(p.read_text()).get("version")
                for p in _all_manifests()}
    distinct = set(versions.values())
    assert len(distinct) == 1, f"manifests disagree on version: {versions}"


def test_changelog_documents_current_version():
    """The shipped version must have a CHANGELOG entry."""
    version = _manifest()["version"]
    changelog = (_PLUGIN_ROOT / "CHANGELOG.md").read_text()
    assert f"[{version}]" in changelog, f"CHANGELOG.md has no entry for {version}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda d: d.name)
def test_skill_name_is_namespaced(skill_dir: Path):
    """Claude Code builds the slash command from frontmatter ``name:`` verbatim.

    The top-level skill stays ``reflect`` (-> /reflect, which the drain calls);
    every other skill must be ``reflect:<skill>`` or it lands in the slash menu
    unnamespaced as /cost, /recall, ... (the bug a1cc2ba set out to fix).
    """
    name = ""
    for line in (skill_dir / "SKILL.md").read_text().splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    assert name, f"{skill_dir.name}/SKILL.md has no 'name:' frontmatter"
    expected = "reflect" if skill_dir.name == "reflect" else f"reflect:{skill_dir.name}"
    assert name == expected, f"{skill_dir.name}/SKILL.md declares name '{name}', expected '{expected}'"
