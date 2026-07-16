#!/usr/bin/env python3
"""Hermes (fleet-lambda) adapter for the reflect-kb plugin.

Hermes has no plugin runtime and no hook autowiring of its own — the
fleet-lambda repo owns the hook wiring that decides *when* the reflect
shims fire. This adapter's job is narrower than the Codex one: physically
deploy the reflect skill content plus a pair of ``shim/`` scripts into
``~/.hermes/`` so a fleet-lambda hook can shell out to them.

Deployed layout::

    plugin/skills/<name>/{hooks,scripts,...}/  → ~/.hermes/skills/<name>/...
    plugin/reflect.toml                        → ~/.hermes/skills/reflect/reflect.toml
    plugin/adapters/hermes/shim/               → ~/.hermes/skills/reflect/shim/

The shims call the deployed ``recall.py`` (shadow recall / capture enqueue);
the fleet-lambda side switches modes via ``FLEET_MEMORY_BACKEND``. Unlike
Codex, this adapter writes NO ``hooks.json`` — hook registration is not ours
to own.

A ``managed_by: reflect-kb/adapters/hermes`` sentinel is injected into each
copied SKILL.md so uninstall stays safe against hand-written siblings.

Usage::

    python hermes_adapter.py install --dry-run
    python hermes_adapter.py install
    python hermes_adapter.py install --force   # overwrite hand-written siblings
    python hermes_adapter.py uninstall
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# Make the shared base importable whether this script is invoked directly or
# through pytest. Mirrors codex_adapter.py's convention.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from base import (  # noqa: E402
    AdapterBase,
    InstallPlan,
    PLUGIN_SKILLS,
    find_plugin_root as _shared_find_plugin_root,
    inject_managed_by as _inject_managed_by,
    run_cli,
)

POINTER_MANAGED_BY = "reflect-kb/adapters/hermes"
HARNESS_DIR = ".hermes"

# Skill subdirectories synced verbatim alongside each SKILL.md so the shim's
# deployed recall.py (skills/recall/scripts/recall.py) resolves at runtime.
SKILL_SUBDIRS: tuple[str, ...] = ("hooks", "scripts", "assets", "references")

# Single-file plugin-root resources copied next to the reflect skill (reflect.toml
# carries the plugin defaults, including the [providers.hermes] block).
PLUGIN_ROOT_FILES: tuple[str, ...] = ("reflect.toml",)


class HermesAdapter(AdapterBase):
    """Hermes harness: full-content skill install + shim deploy, no hooks."""

    POINTER_MANAGED_BY = POINTER_MANAGED_BY
    HARNESS_DIR = HARNESS_DIR
    HARNESS_LABEL = "Hermes"

    # Fallback only — _pointer_body overrides to write full SKILL.md content.
    POINTER_BODY_TEMPLATE = (
        "---\n"
        "name: {name}\n"
        "description: {description}\n"
        "managed_by: {managed_by}\n"
        "source: {source}\n"
        "---\n\n"
        "Adapter could not read the upstream SKILL.md at `{source}` —\n"
        "re-run the install once the source path is accessible.\n"
    )

    def _pointer_body(self, source_skill: Path) -> str:
        """Return the full plugin SKILL.md content with ``managed_by:`` injected.

        Hermes reads skill file content directly (no ``source:`` dereference),
        so mirror the Codex adapter and copy the whole document.
        """
        try:
            text = source_skill.read_text(encoding="utf-8")
        except OSError:
            return super()._pointer_body(source_skill)
        return _inject_managed_by(text, self.POINTER_MANAGED_BY)

    # --- plan augmentation + extras --------------------------------------

    def augment_plan(
        self, plan: InstallPlan, *, home: Path, **kwargs: Any,
    ) -> None:
        plugin_root = self.find_plugin_root()

        # Per-skill subdir syncs. Each entry is (src_dir, dst_dir).
        subdir_syncs: list[tuple[Path, Path]] = []
        for name in PLUGIN_SKILLS:
            src_skill_dir = plugin_root / "skills" / name
            if not src_skill_dir.is_dir():
                continue
            dst_skill_dir = plan.target_harness_dir / "skills" / name
            for subdir in SKILL_SUBDIRS:
                src_sub = src_skill_dir / subdir
                if src_sub.is_dir():
                    subdir_syncs.append((src_sub, dst_skill_dir / subdir))

        reflect_umbrella = plan.target_harness_dir / "skills" / "reflect"

        # Plugin-root single files (reflect.toml) land under the reflect skill.
        root_file_copies: list[tuple[Path, Path]] = []
        for filename in PLUGIN_ROOT_FILES:
            src_file = plugin_root / filename
            if src_file.is_file():
                root_file_copies.append((src_file, reflect_umbrella / filename))

        # Shim scripts a fleet-lambda hook calls. Deployed alongside the
        # reflect skill so the shims resolve recall.py by relative path.
        shim_syncs: list[tuple[Path, Path]] = []
        src_shim = Path(__file__).resolve().parent / "shim"
        if src_shim.is_dir():
            shim_syncs.append((src_shim, reflect_umbrella / "shim"))

        plan.extras["subdir_syncs"] = subdir_syncs
        plan.extras["root_file_copies"] = root_file_copies
        plan.extras["shim_syncs"] = shim_syncs

        describe_extra: list[str] = []
        for src, dst in subdir_syncs:
            describe_extra.append(f"sync dir: {src} → {dst}")
        for src, dst in shim_syncs:
            describe_extra.append(f"sync dir: {src} → {dst}")
        for src, dst in root_file_copies:
            describe_extra.append(f"copy file: {src} → {dst}")
        plan.extras["describe_extra"] = describe_extra

    def execute_extra(
        self, plan: InstallPlan, **kwargs: Any,
    ) -> tuple[list[str], int]:
        actions: list[str] = []

        # 1. Sync per-skill subdirs (merge copy; user-dropped siblings survive).
        for src, dst in plan.extras.get("subdir_syncs", []):
            self._sync_dir(src, dst)
            actions.append(f"synced {dst}")

        # 2. Sync the shim directory.
        for src, dst in plan.extras.get("shim_syncs", []):
            self._sync_dir(src, dst)
            actions.append(f"synced {dst}")

        # 3. Copy plugin-root single files.
        for src, dst in plan.extras.get("root_file_copies", []):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            actions.append(f"copied {dst}")

        return actions, 0

    def uninstall_extra(
        self, *, home: Path, **kwargs: Any,
    ) -> list[str]:
        """Remove the shim dir + reflect.toml this adapter deployed.

        The synced skill subdirs are left in place (they may hold user
        siblings and the SKILL.md sentinel is the authoritative marker that
        the base uninstall keys off). The shim dir and reflect.toml are
        wholly adapter-owned, so it is safe to remove them outright.
        """
        actions: list[str] = []
        reflect_umbrella = home / self.HARNESS_DIR / "skills" / "reflect"

        shim_dir = reflect_umbrella / "shim"
        if shim_dir.is_dir():
            shutil.rmtree(shim_dir, ignore_errors=True)
            actions.append(f"removed {shim_dir}")

        toml = reflect_umbrella / "reflect.toml"
        if toml.exists():
            try:
                toml.unlink()
                actions.append(f"removed {toml}")
            except OSError:
                pass
        return actions

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _sync_dir(src: Path, dst: Path) -> None:
        """Mirror ``src`` into ``dst``, overwriting same-named files.

        Files under ``dst`` that are not in ``src`` survive so user-dropped
        siblings ride through a reinstall. ``__pycache__`` / ``.DS_Store`` are
        skipped so build/IDE noise doesn't ride along.
        """
        dst.mkdir(parents=True, exist_ok=True)
        for entry in src.iterdir():
            if entry.name in ("__pycache__", ".DS_Store"):
                continue
            target = dst / entry.name
            if entry.is_dir():
                HermesAdapter._sync_dir(entry, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)


# --- backwards-compatible module-level API ------------------------------

_DEFAULT_ADAPTER = HermesAdapter(__file__)


def find_plugin_root(script_path: Path | None = None) -> Path:
    return _shared_find_plugin_root(script_path or Path(__file__))


def build_plan(
    *,
    home: Optional[Path] = None,
    plugin_root: Optional[Path] = None,
) -> InstallPlan:
    return _DEFAULT_ADAPTER.build_plan(home=home, plugin_root=plugin_root)


def execute(plan: InstallPlan, *, force: bool = False) -> list[str]:
    actions, _ = _DEFAULT_ADAPTER.execute(plan, force=force)
    return actions


def uninstall(*, home: Optional[Path] = None) -> list[str]:
    return _DEFAULT_ADAPTER.uninstall(home=home)


def _cli() -> int:
    return run_cli(_DEFAULT_ADAPTER)


if __name__ == "__main__":
    sys.exit(_cli())
