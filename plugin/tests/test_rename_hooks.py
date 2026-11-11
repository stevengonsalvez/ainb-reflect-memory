"""Canonical identity works through real lifecycle entrypoints."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
import reflect_gate


@pytest.mark.parametrize("name", ["reflect", "plugly-reflect-memory"])
def test_both_skill_identities_suppress_recursive_capture(tmp_path, name):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"message": {
        "role": "user", "content": f"<command-name>{name}</command-name>\nNo, never use var here."
    }}) + "\n")
    assert reflect_gate.evaluate(transcript).reason == "reflect-on-reflect"


@pytest.mark.parametrize("hook", ["reflect-drain-bg.sh", "idle_reflect.sh", "reflect-maintenance-watch.sh"])
def test_canonical_kill_switch_precedes_legacy(tmp_path, hook):
    state = tmp_path / "state"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("REFLECT_", "PLUGLY_REFLECT_MEMORY_"))}
    env.update(HOME=str(tmp_path), REFLECT_DISABLED="0",
               PLUGLY_REFLECT_MEMORY_DISABLED="1", PLUGLY_REFLECT_MEMORY_STATE_DIR=str(state))
    result = subprocess.run(["bash", str(PLUGIN / "hooks" / hook)], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not state.exists()


def test_canonical_state_overrides_legacy_for_python_producer(tmp_path):
    state = tmp_path / "canonical"
    old = tmp_path / "old"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"message": {"role": "user", "content": "No, never use var here. The root cause was a missing index."}}) + "\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("REFLECT_", "PLUGLY_REFLECT_MEMORY_"))}
    env.update(HOME=str(tmp_path), REFLECT_STATE_DIR=str(old),
               PLUGLY_REFLECT_MEMORY_STATE_DIR=str(state), PLUGLY_REFLECT_MEMORY_AUTO_REFLECT="1")
    result = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "stop_reflect.py")],
        input=json.dumps({"session_id": "rename-proof", "transcript_path": str(transcript), "cwd": str(tmp_path)}),
        env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    queue = state / "pending_reflections.jsonl"
    assert len(queue.read_text().splitlines()) == 1
    assert not old.exists()


def test_legacy_skill_alias_resolves_sibling_under_both_plugin_roots():
    alias = (PLUGIN / "skills" / "reflect" / "SKILL.md").read_text()
    assert "../plugly-reflect-memory/SKILL.md" in alias
    assert "${CLAUDE_PLUGIN_ROOT}/plugin" not in alias
    for root in (PLUGIN, PLUGIN.parent):
        skill_root = root / "skills" if (root / "skills").is_dir() else root / "plugin" / "skills"
        assert (skill_root / "reflect" / ".." / "plugly-reflect-memory" / "SKILL.md").is_file()


def test_git_wrapper_refreshes_owned_paths_without_rechaining(tmp_path):
    import shutil

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    hooks = repo / ".git" / "hooks"
    original = "#!/bin/sh\nexit 0\n"
    (hooks / "post-commit").write_text(original)
    (hooks / "post-commit").chmod(0o755)
    for label in ("old-plugin", "new-plugin"):
        source = tmp_path / label / "hooks"
        source.mkdir(parents=True)
        shutil.copy2(PLUGIN / "hooks" / "install_post_commit.sh", source)
        (source / "post_commit.sh").write_text("#!/bin/sh\nexit 0\n")
        subprocess.run(["bash", str(source / "install_post_commit.sh"), str(repo)],
                       check=True, capture_output=True, text=True)
    wrapper = (hooks / "post-commit").read_text()
    assert str(tmp_path / "new-plugin" / "hooks" / "post_commit.sh") in wrapper
    assert str(tmp_path / "old-plugin") not in wrapper
    assert (hooks / "post-commit.reflect-chained").read_text() == original
    assert "PLUGLY_REFLECT_MEMORY_SCRIPTS_DIR=" in wrapper
