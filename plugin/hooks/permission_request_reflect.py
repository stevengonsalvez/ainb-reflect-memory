#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PermissionRequest policy lookup and watcher arming hook."""

from __future__ import annotations

import json
import time
import sys
import traceback

from hook_common import (  # noqa: E402
    emit_permission_decision,
    forensics_log,
    get_cwd,
    get_session_id,
    get_tool_input,
    get_tool_name,
    high_confidence,
    matching_policy_rules,
    read_stdin_json,
    scrub_secrets,
    state_dir,
    write_last_event,
)


_HOOK_NAME = "permission_request_reflect"


def _arm_permission_watcher(data: dict) -> None:
    session_id = str(get_session_id(data) or "").strip()
    if not session_id:
        return
    tool = str(get_tool_name(data) or "unknown")
    tool_input = get_tool_input(data)
    message = ""
    if isinstance(tool_input, dict):
        message = str(tool_input.get("description") or tool_input.get("command") or "")
    try:
        path = state_dir() / "permission-armed" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool": tool,
            "message": scrub_secrets(message[:500]),
            "title": "PermissionRequest",
            "cwd": str(get_cwd(data) or ""),
            "ts": time.time(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _main_body() -> None:
    data = read_stdin_json()
    _arm_permission_watcher(data)
    rules = matching_policy_rules(data, scope="permission")
    for rule in rules:
        decision = str(rule.get("decision", "")).lower()
        if decision in ("deny", "allow") and high_confidence(rule):
            message = scrub_secrets(str(rule.get("message") or "Reflect permission policy."))
            emit_permission_decision(decision, message if decision == "deny" else "")
            forensics_log(_HOOK_NAME, f"{decision} tool={get_tool_name(data) or '?'}")
            return
    # Empty stdout: decline to decide and let normal approval flow continue.


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
    sys.exit(0)


if __name__ == "__main__":
    main()
