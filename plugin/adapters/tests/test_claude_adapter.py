"""Tests for the Claude Code adapter (plugin/adapters/claude).

``install`` is refused (``claude plugin install`` is the supported path); the
old adapter wrote a SessionStart hook pointing at a script it never deployed.
``uninstall`` stays so older adapter output can be cleaned up. Every test runs
against a throwaway HOME and never touches the invoking user's ~/.claude.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADAPTER_DIR = HERE.parent / "claude"
ADAPTER = ADAPTER_DIR / "claude_adapter.py"

# Make ``claude_adapter`` importable regardless of where pytest runs from.
sys.path.insert(0, str(ADAPTER_DIR))

import claude_adapter  # noqa: E402


@pytest.fixture(autouse=True)
def _sanity():
    assert ADAPTER.exists(), f"missing adapter script at {ADAPTER}"


def _run(*args: str):
    return subprocess.run(
        [sys.executable, str(ADAPTER), *args], capture_output=True, text=True,
    )


def _session_start_commands(claude_dir: Path) -> list[str]:
    cfg = json.loads((claude_dir / "settings.json").read_text())
    return [
        h["command"]
        for entry in cfg.get("hooks", {}).get("SessionStart", [])
        for h in entry["hooks"]
    ]


def _seed_managed_skill(claude_dir: Path, name: str = "recall") -> Path:
    skill = claude_dir / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"---\nname: reflect:{name}\n"
        f"managed_by: {claude_adapter.POINTER_MANAGED_BY}\n---\nbody\n",
        encoding="utf-8",
    )
    return skill


def test_find_plugin_root_resolves_to_reflect_dir():
    root = claude_adapter.find_plugin_root()
    assert (root / "skills").is_dir()
    assert (root / "adapters").is_dir()
    assert (root / "hooks").is_dir()


@pytest.mark.parametrize(
    "flags",
    [[], ["--dry-run"], ["--force"], ["--no-hooks"], ["--force", "--no-hooks"]],
)
def test_install_refuses_and_writes_nothing(tmp_path, flags):
    """No flag combination may create ~/.claude, skills or a hook."""
    result = _run("install", "--home", str(tmp_path), *flags)
    assert result.returncode != 0
    assert "claude plugin marketplace add stevengonsalvez/ainb-reflect-memory" in result.stderr
    assert "claude plugin install reflect@ainb-reflect-memory" in result.stderr
    assert not (tmp_path / ".claude").exists()


def test_install_leaves_existing_settings_untouched(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    original = json.dumps({
        "hooks": {"SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": "echo existing"}]}
        ]}
    })
    (claude_dir / "settings.json").write_text(original)

    result = _run("install", "--home", str(tmp_path))

    assert result.returncode != 0
    assert (claude_dir / "settings.json").read_text() == original
    assert not (claude_dir / "skills").exists()


def test_install_refuses_even_when_plugin_runtime_owns_reflect(tmp_path):
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"reflect@ainb-reflect-memory": [{"scope": "user"}]}}
    ))

    result = _run("install", "--home", str(tmp_path))

    assert result.returncode != 0
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_uninstall_removes_managed_hook_and_keeps_foreign_hooks(tmp_path, legacy):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    managed = (
        claude_adapter._LEGACY_SESSION_START_HOOK_COMMAND
        if legacy
        else claude_adapter._render_session_start_hook_command(claude_dir)
    )
    (claude_dir / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [
                    {"type": "command", "command": "echo foreign"},
                    {"type": "command", "command": managed},
                ]},
                {"matcher": "", "hooks": [{"type": "command", "command": managed}]},
            ],
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo stop"}]}],
        },
    }))

    result = _run("uninstall", "--home", str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "removed SessionStart hook" in result.stdout
    assert _session_start_commands(claude_dir) == ["echo foreign"]
    cfg = json.loads((claude_dir / "settings.json").read_text())
    assert cfg["model"] == "opus"
    assert cfg["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"


def test_uninstall_drops_empty_hooks_block(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    managed = claude_adapter._render_session_start_hook_command(claude_dir)
    (claude_dir / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": managed}]}
        ]}
    }))

    assert _run("uninstall", "--home", str(tmp_path)).returncode == 0

    assert json.loads((claude_dir / "settings.json").read_text()) == {}


def test_uninstall_removes_only_managed_skill_files(tmp_path):
    claude_dir = tmp_path / ".claude"
    managed = _seed_managed_skill(claude_dir, "recall")
    note = managed.parent / "user-note.md"
    note.write_text("hand-written", encoding="utf-8")
    foreign = claude_dir / "skills" / "reflect" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("---\nname: user-handwritten\n---\nbody\n", encoding="utf-8")

    result = _run("uninstall", "--home", str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert not managed.exists()
    assert note.exists()
    assert foreign.exists()


def test_uninstall_no_hooks_leaves_settings_alone(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    managed = claude_adapter._render_session_start_hook_command(claude_dir)
    original = json.dumps({"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": managed}]}
    ]}})
    (claude_dir / "settings.json").write_text(original)

    assert _run("uninstall", "--home", str(tmp_path), "--no-hooks").returncode == 0

    assert (claude_dir / "settings.json").read_text() == original


def test_uninstall_is_idempotent_and_safe_on_empty_home(tmp_path):
    for _ in range(2):
        result = _run("uninstall", "--home", str(tmp_path))
        assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".claude").exists()


def test_uninstall_skips_corrupt_settings_json(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{this is not json")

    result = _run("uninstall", "--home", str(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "not valid JSON" in result.stdout
    assert (claude_dir / "settings.json").read_text() == "{this is not json"
