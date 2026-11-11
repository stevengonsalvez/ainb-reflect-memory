"""Backed-up adapter identity migration without moving memory or runtime state.

Inventory is read-only. Apply journals a recoverable snapshot before invoking an
adapter; rollback refuses to overwrite edits made since the completed migration.
Claude native plugin and launchd cutovers remain explicit operator actions.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import click

HARNESSES = ("codex", "copilot", "hermes")


def _plugin_root() -> Path:
    bundled = Path(__file__).parent / "_plugin"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[2] / "plugin"


def _paths(home: Path, harness: str) -> list[Path]:
    paths = [home / f".{harness}" / "skills"]
    if harness == "codex":
        paths.append(home / ".codex" / "hooks.json")
    if harness == "copilot":
        paths.append(home / ".copilot" / "hooks")
    return paths


def _safe_tree(path: Path, home: Path) -> None:
    # Refuse symlink traversal instead of snapshotting outside the selected home.
    current = path
    while current != home:
        if current.is_symlink():
            raise click.ClickException(f"Refusing symlink path: {current}")
        current = current.parent
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise click.ClickException(f"Refusing symlink path: {child}")
            if not (child.is_dir() or child.is_file()):
                raise click.ClickException(f"Unsupported file type: {child}")


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    else:
        for entry in sorted(path.rglob("*")):
            digest.update(str(entry.relative_to(path)).encode())
            digest.update(b"d" if entry.is_dir() else b"f")
            if entry.is_file():
                digest.update(entry.read_bytes())
    return digest.hexdigest()


def _write_journal(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def _locked(home: Path):
    root = home / ".reflect" / "rename-migrations"
    _safe_tree(root, home)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with (root / "migration.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise click.ClickException("Another identity migration is active") from None
        yield root


def _restore(home: Path, backup: Path, journal: dict) -> None:
    # Validate every snapshot before deleting even one live path. A missing or
    # corrupt backup must never turn rollback into data loss.
    for item in journal["paths"]:
        source = backup / "files" / item["path"]
        _safe_tree(source, home)
        if item["existed"] and (
            "before" not in item or _digest(source) != item["before"]
        ):
            raise click.ClickException("Backup integrity check failed; live files left untouched")
    journal["state"] = "restoring"
    _write_journal(backup / "journal.json", journal)
    for index, item in enumerate(journal["paths"]):
        target = home / item["path"]
        _safe_tree(target, home)
        progress = item.get("restore_state", "pending")
        expected = item.get("before")
        actual = _digest(target)
        if progress == "restored":
            if actual != expected:
                raise click.ClickException(f"Changed after restoration; refusing overwrite: {target}")
            continue
        if progress == "pending" and "after" in item and actual != item["after"]:
            raise click.ClickException(f"Changed since migration; refusing overwrite: {target}")
        # Journal before destructive work. A failed/interrupting copy is retried
        # from the immutable snapshot, including when a live path is absent.
        item["restore_state"] = "restoring"
        _write_journal(backup / "journal.json", journal)
        stage = backup / f"restore-{index}"
        if stage.is_dir():
            shutil.rmtree(stage)
        elif stage.exists():
            stage.unlink()
        if item["existed"]:
            source = backup / "files" / item["path"]
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                shutil.copy2(source, stage)
            if _digest(stage) != expected:
                raise click.ClickException("Staged restore failed integrity check")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if item["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage), str(target))
        item["restore_state"] = "restored"
        _write_journal(backup / "journal.json", journal)


@click.group("migrate")
def migration_group():
    """Inspect, migrate, or roll back adapter identity registrations."""


@migration_group.command("inspect")
@click.option("--home", type=click.Path(path_type=Path), default=Path.home)
def inspect(home: Path):
    """Read-only inventory, without dumping configuration or memory contents."""
    home = home.expanduser().resolve()
    inventory = {}
    for harness in HARNESSES:
        inventory[harness] = {
            str(path.relative_to(home)): path.exists() for path in _paths(home, harness)
        }
    for relative in (
        ".claude/plugins/installed_plugins.json", ".learnings", ".reflect",
        "Library/LaunchAgents/com.reflect.drain.plist",
    ):
        inventory[relative] = (home / relative).exists()
    click.echo(json.dumps(inventory, indent=2))


@migration_group.command("apply")
@click.option("--home", type=click.Path(path_type=Path), default=Path.home)
@click.option("--harness", type=click.Choice(HARNESSES), required=True)
def apply(home: Path, harness: str):
    """Migrate one adapter, saving a recoverable snapshot first.

    Close active harness sessions before applying. This command does not change
    native Claude plugin registrations, launchd jobs, git hooks, or memory data.
    """
    home = home.expanduser().resolve()
    script = _plugin_root() / "adapters" / harness / f"{harness}_adapter.py"
    if not script.is_file():
        raise click.ClickException("Bundled adapter is missing; reinstall the package")
    paths = _paths(home, harness)
    for path in paths:
        _safe_tree(path, home)
    with _locked(home) as root:
        for unfinished in root.glob("*/journal.json"):
            if json.loads(unfinished.read_text())["state"] in {"prepared", "applying", "restoring"}:
                raise click.ClickException(
                    f"Incomplete migration requires rollback: {unfinished.parent.name}"
                )
        transaction = uuid.uuid4().hex
        backup = root / transaction
        backup.mkdir(mode=0o700)
        journal = {"version": 1, "home": str(home), "harness": harness,
                   "state": "prepared", "paths": []}
        for path in paths:
            relative = str(path.relative_to(home))
            journal["paths"].append({"path": relative, "existed": path.exists(),
                                     "before": _digest(path)})
            if path.exists():
                destination = backup / "files" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, destination)
                else:
                    shutil.copy2(path, destination)
                expected = journal["paths"][-1]["before"]
                if _digest(destination) != expected or _digest(path) != expected:
                    raise click.ClickException("Files changed during snapshot; migration not started")
        journal_path = backup / "journal.json"
        _write_journal(journal_path, journal)
        journal["state"] = "applying"
        _write_journal(journal_path, journal)
        try:
            subprocess.run(
                [sys.executable, str(script), "install", "--home", str(home)],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            _restore(home, backup, journal)
            journal["state"] = "rolled_back"
            _write_journal(journal_path, journal)
            # Adapter output can contain user configuration; never echo it here.
            raise click.ClickException(
                f"Adapter migration failed ({type(exc).__name__}); snapshot restored. "
                f"Transaction: {transaction}"
            ) from None
        for item in journal["paths"]:
            item["after"] = _digest(home / item["path"])
        journal["state"] = "complete"
        _write_journal(journal_path, journal)
        click.echo(f"Migration complete. Transaction: {transaction}")


@migration_group.command("rollback")
@click.argument("transaction")
@click.option("--home", type=click.Path(path_type=Path), default=Path.home)
def rollback(transaction: str, home: Path):
    """Restore a transaction; reject edits made after completed migration."""
    if len(transaction) != 32 or any(c not in "0123456789abcdef" for c in transaction):
        raise click.BadParameter("Expected the transaction ID printed by apply")
    home = home.expanduser().resolve()
    with _locked(home) as root:
        backup = root / transaction
        _safe_tree(backup, home)
        journal_path = backup / "journal.json"
        if not journal_path.is_file():
            raise click.ClickException("Unknown migration transaction")
        journal = json.loads(journal_path.read_text())
        if journal.get("home") != str(home) or journal.get("version") != 1:
            raise click.ClickException("Migration journal does not match this home")
        expected_paths = {str(p.relative_to(home)) for p in _paths(home, journal["harness"])}
        if {item["path"] for item in journal["paths"]} != expected_paths:
            raise click.ClickException("Migration journal contains unexpected paths")
        if journal["state"] == "rolled_back":
            click.echo("Transaction already rolled back")
            return
        for item in journal["paths"]:
            target = home / item["path"]
            _safe_tree(target, home)
            if journal["state"] == "complete" and _digest(target) != item["after"]:
                raise click.ClickException(f"Changed since migration; refusing overwrite: {target}")
        _restore(home, backup, journal)
        journal["state"] = "rolled_back"
        _write_journal(journal_path, journal)
        click.echo("Migration rolled back")
