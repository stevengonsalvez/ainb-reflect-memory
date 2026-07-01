#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SessionEnd final cleanup and queue producer hook."""

from __future__ import annotations

import sys
import traceback

from hook_common import (  # noqa: E402
    enqueue_reflection,
    forensics_log,
    get_transcript_path,
    read_stdin_json,
    write_last_event,
)


_HOOK_NAME = "session_end_reflect"


def _main_body() -> None:
    data = read_stdin_json()
    transcript = str(get_transcript_path(data) or "").strip()
    queued = enqueue_reflection(
        trigger="session_end",
        data=data,
        transcript_path=transcript,
        scope="session",
    )
    forensics_log(_HOOK_NAME, f"queued={queued}")


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
