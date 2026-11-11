"""Behavioral regression coverage for canonical identity upgrades."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTERS = Path(__file__).resolve().parents[1]


def install(harness, home):
    result = subprocess.run(
        [sys.executable, str(ADAPTERS / harness / f"{harness}_adapter.py"),
         "install", "--home", str(home)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("harness", ["codex", "copilot", "hermes"])
def test_upgrade_owned_skills_and_canonical_umbrella(tmp_path, harness):
    recall = tmp_path / f".{harness}" / "skills" / "recall" / "SKILL.md"
    recall.parent.mkdir(parents=True)
    recall.write_text(f"---\nname: reflect:recall\nmanaged_by: reflect-kb/adapters/{harness}\n---\nlegacy")
    install(harness, tmp_path)
    assert "name: plugly-reflect-memory:recall" in recall.read_text()
    canonical = recall.parents[1] / "plugly-reflect-memory" / "SKILL.md"
    assert canonical.exists()
    assert f"managed_by: plugly-reflect-memory/adapters/{harness}" in canonical.read_text()
    legacy = recall.parents[1] / "reflect" / "SKILL.md"
    assert "compatibility" in legacy.read_text().lower()


def test_codex_upgrade_replaces_legacy_commands_once(tmp_path):
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    command = f"uv run {hooks.parent}/skills/reflect/hooks/stop_reflect.py"
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": command},
        {"type": "command", "command": "echo unrelated"},
    ]}]}}))
    install("codex", tmp_path)
    first = hooks.read_text()
    commands = [h["command"] for g in json.loads(first)["hooks"]["Stop"] for h in g["hooks"]]
    assert commands.count("echo unrelated") == 1
    assert command not in commands
    assert sum("plugly-reflect-memory/hooks/stop_reflect.py" in c for c in commands) == 1
    install("codex", tmp_path)
    assert hooks.read_text() == first


def test_copilot_upgrade_removes_only_owned_legacy_dropin(tmp_path):
    hooks = tmp_path / ".copilot" / "hooks"
    hooks.mkdir(parents=True)
    old = hooks / "reflect.json"
    old.write_text(json.dumps({"version": 1, "hooks": {"agentStop": [{
        "type": "command", "command": f"REFLECT_HARNESS=copilot uv run {tmp_path}/.copilot/skills/reflect/hooks/stop_reflect.py"
    }]}}))
    unrelated = hooks / "other.json"
    unrelated.write_text('{"version": 1, "hooks": {}}')
    install("copilot", tmp_path)
    assert not old.exists()
    assert (hooks / "plugly-reflect-memory.json").exists()
    assert unrelated.read_text() == '{"version": 1, "hooks": {}}'


def test_copilot_uninstall_removes_recognized_legacy_dropin(tmp_path):
    hooks = tmp_path / ".copilot" / "hooks"
    hooks.mkdir(parents=True)
    legacy = hooks / "reflect.json"
    legacy.write_text(json.dumps({"version": 1, "hooks": {"agentStop": [{
        "type": "command", "command": f"REFLECT_HARNESS=copilot uv run {tmp_path}/.copilot/skills/reflect/hooks/stop_reflect.py"
    }]}}))
    result = subprocess.run([sys.executable, str(ADAPTERS / "copilot" / "copilot_adapter.py"),
                             "uninstall", "--home", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not legacy.exists()


def test_copilot_refuses_unknown_legacy_dropin(tmp_path):
    hooks = tmp_path / ".copilot" / "hooks"
    hooks.mkdir(parents=True)
    legacy = hooks / "reflect.json"
    content = '{"version": 1, "hooks": {"agentStop": [{"type":"command","command":"echo foreign"}]}}'
    legacy.write_text(content)
    result = subprocess.run([sys.executable, str(ADAPTERS / "copilot" / "copilot_adapter.py"),
                             "install", "--home", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert legacy.read_text() == content
    assert not (hooks / "plugly-reflect-memory.json").exists()


@pytest.mark.parametrize("harness", ["codex", "copilot", "hermes"])
def test_foreign_canonical_skill_preflight_leaves_entire_install_untouched(tmp_path, harness):
    root = tmp_path / f".{harness}"
    foreign = root / "skills" / "plugly-reflect-memory"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: private-memory\n---\nUser-owned skill.\n")
    (foreign / "scripts").mkdir()
    (foreign / "scripts" / "identity_env.py").write_text("# user-owned implementation\n")
    hook = root / "hooks.json"
    hook.write_text('{"hooks":{"Stop":[{"hooks":[{"command":"echo private"}]}]}}')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = subprocess.run([sys.executable, str(ADAPTERS / harness / f"{harness}_adapter.py"),
                             "install", "--home", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
