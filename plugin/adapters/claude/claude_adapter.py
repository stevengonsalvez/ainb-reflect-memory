#!/usr/bin/env python3
"""Claude Code adapter for the reflect-kb plugin: uninstall-only.

``install`` is intentionally refused. Claude Code installs reflect through its
native plugin runtime, which extracts the whole plugin (hook scripts included)
into its cache and wires every hook from ``plugin/.claude-plugin/plugin.json``::

    claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
    claude plugin install reflect@ainb-reflect-memory

Earlier versions of this adapter copied only each ``SKILL.md`` into
``~/.claude/skills/<name>/`` and merged a SessionStart entry into
``~/.claude/settings.json`` pointing at
``~/.claude/skills/recall/hooks/session_start_recall.py``, a file the adapter
never deployed. The result was a dead hook that failed on every session start.
Rather than keep a second, broken install path, ``install`` now exits non-zero
(also under ``--dry-run``) and writes nothing.

``uninstall`` stays so users who ran an older adapter can clean up. It removes:

  * ``~/.claude/skills/<name>/SKILL.md`` files carrying the
    ``managed_by: reflect-kb/adapters/claude`` sentinel (hand-written files
    without the sentinel are left alone), and
  * the SessionStart hook entries the old adapter wrote into ``settings.json``
    (matched by exact command; foreign hooks are preserved).

Usage::

    python claude_adapter.py uninstall
    python claude_adapter.py uninstall --no-hooks   # leave settings.json alone
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

# Make the shared base importable whether the script is invoked directly
# (``python claude_adapter.py uninstall``) or through pytest. We deliberately
# avoid turning ``adapters/`` into a proper package because the per-harness
# scripts already work as standalone executables.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from base import (  # noqa: E402
    AdapterBase,
    PLUGIN_SKILLS,  # re-exported for backwards-compat with tests
    find_plugin_root as _shared_find_plugin_root,
    run_cli,
)

# Sentinel the old adapter wrote into each copied SKILL.md's ``managed_by:``
# field. Uninstall uses it to tell adapter-written files from hand-written ones.
POINTER_MANAGED_BY = "reflect-kb/adapters/claude"

# Template for the SessionStart hook command the old adapter wrote.
# ``{home_tool_dir}`` was substituted with the resolved Claude home
# (e.g. ``~/.claude``). Kept only so uninstall can recognise those entries.
SESSION_START_HOOK_COMMAND_TEMPLATE = (
    "uv run {home_tool_dir}/skills/recall/hooks/session_start_recall.py"
)

INSTALL_REFUSED_MESSAGE = """\
claude_adapter.py install is not supported. Nothing was written.

Install reflect into Claude Code with the native plugin runtime instead. It
deploys the hook scripts and wires every hook from the plugin manifest:

    claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
    claude plugin install reflect@ainb-reflect-memory

Then restart Claude Code. See docs-site/src/content/docs/install/claude-code.md
(or plugin/README.md) for the reflect CLI engine step and verification.

To clean up skills and the SessionStart hook an older adapter wrote:

    python claude_adapter.py uninstall
"""


def _render_session_start_hook_command(claude_dir: Path) -> str:
    """Substitute the resolved Claude home into the hook command template."""
    return SESSION_START_HOOK_COMMAND_TEMPLATE.format(home_tool_dir=str(claude_dir))


# Legacy literal that older buggy installs persisted into settings.json.
# Kept as a constant so uninstall can still sweep it.
_LEGACY_SESSION_START_HOOK_COMMAND = SESSION_START_HOOK_COMMAND_TEMPLATE.replace(
    "{home_tool_dir}", "{{HOME_TOOL_DIR}}"
)


class ClaudeAdapter(AdapterBase):
    """Claude harness: install refused, uninstall of old adapter output kept."""

    POINTER_MANAGED_BY = POINTER_MANAGED_BY
    HARNESS_DIR = ".claude"
    HARNESS_LABEL = "Claude"

    # --- CLI -------------------------------------------------------------

    def _cli(self, argv: Optional[Sequence[str]] = None) -> int:
        args = list(sys.argv[1:] if argv is None else argv)
        if args and args[0] == "install":
            # Refuse before argparse and before any filesystem access, so no
            # flag combination (--force, --dry-run, --no-hooks) can write a
            # dead hook.
            print(INSTALL_REFUSED_MESSAGE, file=sys.stderr, end="")
            return 2
        return super()._cli(argv)

    def configure_uninstall_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--no-hooks", action="store_true",
            help="Leave settings.json untouched; only remove managed skill files.",
        )

    def uninstall_kwargs_from_args(self, args: argparse.Namespace) -> dict[str, Any]:
        return {"with_hooks": not getattr(args, "no_hooks", False)}

    # --- uninstall -------------------------------------------------------

    def uninstall_extra(
        self, *, home: Path, with_hooks: bool = True, **kwargs: Any,
    ) -> list[str]:
        if not with_hooks:
            return []
        settings_path = home / self.HARNESS_DIR / "settings.json"
        if not settings_path.exists():
            return []
        try:
            cfg = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return [
                f"settings.json is not valid JSON; "
                f"skipped hook removal: {settings_path}"
            ]
        wanted_command = _render_session_start_hook_command(settings_path.parent)
        # Match both the rendered command (current installs) and the
        # legacy unsubstituted template (broken-by-bootstrap installs).
        removable = {wanted_command, _LEGACY_SESSION_START_HOOK_COMMAND}
        if not isinstance(cfg, dict):
            return [
                f"settings.json is not a JSON object; "
                f"skipped hook removal: {settings_path}"
            ]
        # settings.json is user-edited: `hooks` may be null, an event value may
        # not be a list, an entry may not be a dict. Anything that is not
        # recognisably ours is foreign and is left exactly as found.
        hooks = cfg.get("hooks")
        ss = hooks.get("SessionStart") if isinstance(hooks, dict) else None
        if not isinstance(ss, list):
            return []
        filtered: list = []
        changed = False
        for entry in ss:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(inner, list):
                filtered.append(entry)
                continue
            kept_hooks = [
                h for h in inner
                if not (isinstance(h, dict) and isinstance(h.get("command"), str)
                        and h["command"] in removable)
            ]
            if len(kept_hooks) == len(inner):
                filtered.append(entry)
                continue
            changed = True
            if kept_hooks:
                new_entry = dict(entry)
                new_entry["hooks"] = kept_hooks
                filtered.append(new_entry)
        if not changed:
            return []
        if filtered:
            hooks["SessionStart"] = filtered
        else:
            hooks.pop("SessionStart", None)
        if not hooks:
            cfg.pop("hooks", None)
        settings_path.write_text(
            json.dumps(cfg, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return [f"removed SessionStart hook from {settings_path}"]


# --- backwards-compatible module-level API ------------------------------

_DEFAULT_ADAPTER = ClaudeAdapter(__file__)


def find_plugin_root(script_path: Path | None = None) -> Path:
    """Walk up from this script (or ``script_path``) to the plugin root."""
    return _shared_find_plugin_root(script_path or Path(__file__))


def uninstall(
    *, home: Optional[Path] = None, with_hooks: bool = True,
) -> list[str]:
    """Remove managed skill files and our SessionStart hook entry. Idempotent."""
    return _DEFAULT_ADAPTER.uninstall(home=home, with_hooks=with_hooks)


def _cli() -> int:
    return run_cli(_DEFAULT_ADAPTER)


if __name__ == "__main__":
    sys.exit(_cli())
