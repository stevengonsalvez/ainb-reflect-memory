"""Behavioral coverage for public aliases and recoverable adapter cutover."""
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from reflect_kb import migration
from reflect_kb.cli.main import main


def invoke(*args):
    return CliRunner().invoke(main, ["migrate", *map(str, args)])


def test_module_aliases_share_class_and_singleton_identity():
    for name in ("errors", "migration", "recall.corpus", "cli.main"):
        old = importlib.import_module(f"reflect_kb.{name}")
        new = importlib.import_module(f"plugly_reflect_memory.{name}")
        assert old is new


@pytest.mark.parametrize("module", ["plugly_reflect_memory", "plugly_reflect_memory.cli.main"])
def test_module_console_entrypoint(module):
    result = subprocess.run([sys.executable, "-m", module, "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "migrate" in result.stdout


def test_inspect_creates_no_files_or_exposes_contents(tmp_path):
    settings = tmp_path / ".codex" / "hooks.json"
    settings.parent.mkdir()
    settings.write_text('{"private": "synthetic-private-value"}')
    before = sorted(tmp_path.rglob("*"))
    result = invoke("inspect", "--home", tmp_path)
    assert result.exit_code == 0, result.output
    assert 'synthetic-private-value' not in result.output
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("harness", migration.HARNESSES)
def test_apply_repeat_rollback_preserves_original_files(tmp_path, harness):
    skills = tmp_path / f".{harness}" / "skills"
    foreign = skills / "unrelated" / "SKILL.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("untouched")
    original = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    first = invoke("apply", "--home", tmp_path, "--harness", harness)
    assert first.exit_code == 0, first.output
    transaction = first.output.strip().split()[-1]
    assert (skills / "plugly-reflect-memory" / "SKILL.md").exists()
    second = invoke("apply", "--home", tmp_path, "--harness", harness)
    assert second.exit_code == 0, second.output
    second_tx = second.output.strip().split()[-1]
    assert invoke("rollback", second_tx, "--home", tmp_path).exit_code == 0
    rollback = invoke("rollback", transaction, "--home", tmp_path)
    assert rollback.exit_code == 0, rollback.output
    for relative, value in original.items():
        assert (tmp_path / relative).read_bytes() == value
    assert not (skills / "plugly-reflect-memory").exists()
    assert invoke("rollback", transaction, "--home", tmp_path).exit_code == 0
    assert not (tmp_path / ".learnings").exists()


def test_failed_apply_restores_snapshot_without_leaking_output(tmp_path, monkeypatch):
    skills = tmp_path / ".codex" / "skills"
    skills.mkdir(parents=True)
    original = skills / "original"
    original.write_text("keep")

    def fail(*args, **kwargs):
        original.write_text("partial mutation")
        raise subprocess.CalledProcessError(2, "adapter", output="synthetic-private-value")

    monkeypatch.setattr(migration.subprocess, "run", fail)
    result = invoke("apply", "--home", tmp_path, "--harness", "codex")
    assert result.exit_code != 0
    assert original.read_text() == "keep"
    assert "snapshot restored" in result.output
    assert "synthetic-private-value" not in result.output


def test_rollback_refuses_post_migration_edits(tmp_path):
    result = invoke("apply", "--home", tmp_path, "--harness", "hermes")
    assert result.exit_code == 0, result.output
    tx = result.output.strip().split()[-1]
    skill = tmp_path / ".hermes" / "skills" / "plugly-reflect-memory" / "SKILL.md"
    skill.write_text("new user edits")
    result = invoke("rollback", tx, "--home", tmp_path)
    assert result.exit_code != 0
    assert skill.read_text() == "new user edits"


def test_symlink_escape_refused_before_mutation(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".codex").symlink_to(outside, target_is_directory=True)
    result = invoke("apply", "--home", home, "--harness", "codex")
    assert result.exit_code != 0
    assert "symlink" in result.output
    assert not list(outside.iterdir())


def test_incomplete_transaction_requires_explicit_rollback(tmp_path):
    root = tmp_path / ".reflect" / "rename-migrations"
    tx = "a" * 32
    backup = root / tx
    backup.mkdir(parents=True)
    (backup / "journal.json").write_text(json.dumps({
        "version": 1, "home": str(tmp_path), "harness": "codex", "state": "applying",
        "paths": [{"path": ".codex/skills", "existed": False},
                  {"path": ".codex/hooks.json", "existed": False}],
    }))
    result = invoke("apply", "--home", tmp_path, "--harness", "codex")
    assert result.exit_code != 0
    assert "Incomplete migration" in result.output
    result = invoke("rollback", tx, "--home", tmp_path)
    assert result.exit_code == 0, result.output


def test_transaction_id_rejects_path_traversal(tmp_path):
    result = invoke("rollback", "../../unrelated", "--home", tmp_path)
    assert result.exit_code != 0
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize("damage", ["missing_file", "missing_tree", "corrupt_bytes"])
def test_damaged_backup_never_deletes_live_files(tmp_path, damage):
    precious = tmp_path / ".hermes" / "skills" / "precious" / "SKILL.md"
    precious.parent.mkdir(parents=True)
    precious.write_text("original user file")
    result = invoke("apply", "--home", tmp_path, "--harness", "hermes")
    assert result.exit_code == 0, result.output
    tx = result.output.strip().split()[-1]
    backup_file = tmp_path / ".reflect" / "rename-migrations" / tx / "files" / ".hermes" / "skills" / "precious" / "SKILL.md"
    if damage == "corrupt_bytes":
        backup_file.write_text("corrupt")
    elif damage == "missing_file":
        backup_file.unlink()
    else:
        import shutil
        shutil.rmtree(backup_file.parents[1])
    before = {str(p.relative_to(tmp_path / ".hermes")): p.read_bytes()
              for p in (tmp_path / ".hermes").rglob("*") if p.is_file()}
    result = invoke("rollback", tx, "--home", tmp_path)
    assert result.exit_code != 0
    assert "integrity" in result.output
    after = {str(p.relative_to(tmp_path / ".hermes")): p.read_bytes()
             for p in (tmp_path / ".hermes").rglob("*") if p.is_file()}
    assert before == after


def test_interrupted_rollback_resumes_without_losing_original_hooks(tmp_path, monkeypatch):
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo unrelated"}]}]}}')
    original = hooks.read_bytes()
    result = invoke("apply", "--home", tmp_path, "--harness", "codex")
    assert result.exit_code == 0, result.output
    tx = result.output.strip().split()[-1]
    real_move = migration.shutil.move
    interrupted = False

    def interrupt_move(source, destination, *args, **kwargs):
        nonlocal interrupted
        if Path(destination) == hooks and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return real_move(source, destination, *args, **kwargs)

    monkeypatch.setattr(migration.shutil, "move", interrupt_move)
    first = invoke("rollback", tx, "--home", tmp_path)
    assert first.exit_code != 0
    journal = json.loads((tmp_path / ".reflect" / "rename-migrations" / tx / "journal.json").read_text())
    assert journal["state"] == "restoring"
    assert invoke("apply", "--home", tmp_path, "--harness", "codex").exit_code != 0
    second = invoke("rollback", tx, "--home", tmp_path)
    assert second.exit_code == 0, second.output
    assert hooks.read_bytes() == original
    assert not (tmp_path / ".codex" / "skills").exists()


def test_public_inventory_does_not_initialize_knowledge_base(tmp_path, monkeypatch):
    from reflect_kb.cli import learnings_cli
    kb = tmp_path / "absent-kb"
    monkeypatch.setattr(learnings_cli, "DEFAULT_REPO_PATH", kb)
    monkeypatch.delenv("GLOBAL_LEARNINGS_PATH", raising=False)
    result = invoke("inspect", "--home", tmp_path)
    assert result.exit_code == 0, result.output
    assert not kb.exists()


def test_canonical_scheduler_templates_keep_shared_state_and_point_to_shipped_files():
    import plistlib
    plugin = migration._plugin_root()
    for job in ("drain", "idle", "maintenance", "forget", "synthesis"):
        path = plugin / "launchd" / f"com.plugly-reflect-memory.{job}.plist"
        rendered = path.read_text().replace("{{PLUGIN_ROOT}}", str(plugin))
        doc = plistlib.loads(rendered.encode())
        assert doc["Label"] == f"com.plugly-reflect-memory.{job}"
        script = next(arg for arg in doc["ProgramArguments"] if str(plugin) in arg)
        assert Path(script).is_file()
        if job == "maintenance":
            env = doc["EnvironmentVariables"]
            assert env["PLUGLY_REFLECT_MEMORY_WATCH_DRAIN_LABEL"] == "com.plugly-reflect-memory.drain"
    assert (plugin / "launchd" / "com.reflect.drain.plist").exists()
