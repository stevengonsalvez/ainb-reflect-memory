#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Explicit PostToolUseFailure mini-learning arming hook."""

from __future__ import annotations

import json
import sys
import time
import traceback

from hook_common import (  # noqa: E402
    forensics_log,
    get_session_id,
    get_tool_input,
    get_tool_name,
    get_tool_response,
    read_stdin_json,
    scrub_secrets,
    state_dir,
    write_last_event,
)


_HOOK_NAME = "posttoolusefailure_minilearning"


def _main_body() -> None:
    data = read_stdin_json()
    session_id = str(get_session_id(data) or "").strip()
    if not session_id:
        return
    tool_response = get_tool_response(data)
    if not isinstance(tool_response, dict):
        tool_response = {"error": str(tool_response)}
    tool_response.setdefault("is_error", True)
    try:
        path = state_dir() / "armed" / f"{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tool": str(get_tool_name(data) or "unknown"),
            "tool_input": scrub_secrets(str(get_tool_input(data))[:500]),
            "tool_response": scrub_secrets(json.dumps(tool_response)[:500]),
            "reason": "failure",
            "ts": time.time(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        forensics_log(_HOOK_NAME, f"armed session={session_id[:8]}")
    except Exception:
        pass


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
