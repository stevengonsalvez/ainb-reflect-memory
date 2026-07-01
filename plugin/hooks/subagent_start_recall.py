#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SubagentStart scoped recall hook.

Injects small, subagent-scoped context before a subagent starts. This is an
ambient bootstrap like SessionStart, but the query is shaped by agent type and
parent task instead of only cwd/branch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from hook_common import (  # noqa: E402
    emit_additional_context,
    forensics_log,
    get_agent_id,
    get_agent_type,
    get_cwd,
    get_prompt,
    read_stdin_json,
    scrub_secrets,
    write_last_event,
)


_HOOK_NAME = "subagent_start_recall"
_EVENT_NAME = "SubagentStart"
_RECALL_TIMEOUT = float(os.environ.get("REFLECT_SUBAGENT_RECALL_TIMEOUT", "5"))
_RECALL_LIMIT = os.environ.get("REFLECT_SUBAGENT_RECALL_LIMIT", "3")
_RECALL_MAX_CHARS = os.environ.get("REFLECT_SUBAGENT_RECALL_MAX_CHARS", "1500")


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_recall_script() -> Path | None:
    root = _plugin_root()
    candidates = [
        root / "skills" / "recall" / "scripts" / "recall.py",
        root.parent / "recall" / "scripts" / "recall.py",
        root / "scripts" / "recall.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _build_query(data: dict) -> str:
    agent_type = str(get_agent_type(data) or "subagent")
    agent_id = str(get_agent_id(data) or "")
    prompt = str(get_prompt(data) or data.get("task") or data.get("description") or "")
    cwd = str(get_cwd(data) or os.getcwd())
    parts = [f"subagent {agent_type}", f"cwd {cwd}"]
    if agent_id:
        parts.append(f"agent_id {agent_id}")
    if prompt:
        parts.append(prompt)
    return " | ".join(parts)


def _query_recall(query: str) -> str:
    override = os.environ.get("REFLECT_SUBAGENT_CONTEXT")
    if override is not None:
        return override.strip()
    recall = _find_recall_script()
    uv = shutil.which("uv")
    if not recall or not uv:
        return ""
    try:
        result = subprocess.run(
            [
                uv,
                "run",
                "--quiet",
                str(recall),
                query,
                "--limit",
                str(_RECALL_LIMIT),
                "--confidence",
                "ANY",
                "--format",
                "markdown",
                "--max-chars",
                str(_RECALL_MAX_CHARS),
                "--tags",
                "",
                "--no-gap-log",
                "--no-followup",
            ],
            capture_output=True,
            text=True,
            timeout=_RECALL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _main_body() -> None:
    data = read_stdin_json()
    query = _build_query(data)
    context = _query_recall(query)
    if context:
        context = f"## Prior learnings for subagent `{scrub_secrets(query[:100])}`\n{context}"
    emit_additional_context(_EVENT_NAME, context)
    forensics_log(_HOOK_NAME, f"agent={get_agent_type(data) or '?'} injected={bool(context)}")


def main() -> None:
    try:
        _main_body()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        detail = str(exc) or traceback.format_exc(limit=2)
        write_last_event(
            hook_name=_HOOK_NAME,
            event="error",
            kind=type(exc).__name__,
            detail=detail,
        )
        forensics_log(_HOOK_NAME, f"{type(exc).__name__}: {detail}")
        emit_additional_context(_EVENT_NAME, "")
    sys.exit(0)


if __name__ == "__main__":
    main()
