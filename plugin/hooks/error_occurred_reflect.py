#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Copilot errorOccurred breadcrumb hook."""

from __future__ import annotations

import sys
import traceback

from hook_common import (  # noqa: E402
    forensics_log,
    get_session_id,
    read_stdin_json,
    scrub_secrets,
    state_dir,
    write_jsonl,
    write_last_event,
)


_HOOK_NAME = "error_occurred_reflect"


def _main_body() -> None:
    data = read_stdin_json()
    payload = {
        "session_id": get_session_id(data) or "",
        "kind": data.get("kind") or data.get("errorType") or data.get("type") or "error",
        "message": scrub_secrets(str(data.get("message") or data.get("error") or ""))[:500],
        "cwd": data.get("cwd") or "",
    }
    write_jsonl(state_dir() / "errors.jsonl", payload)
    forensics_log(_HOOK_NAME, f"recorded kind={payload['kind']}")


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
