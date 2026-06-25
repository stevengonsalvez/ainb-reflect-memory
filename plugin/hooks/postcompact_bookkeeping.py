#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PostCompact bookkeeping hook.

No recall, no queue append, no drain. Optional dedupe reset is guarded by
REFLECT_POSTCOMPACT_RESET_DEDUPE=1.
"""

from __future__ import annotations

import os
import sys
import traceback

from hook_common import (  # noqa: E402
    forensics_log,
    get_session_id,
    read_stdin_json,
    state_dir,
    write_last_event,
)


_HOOK_NAME = "postcompact_bookkeeping"


def _main_body() -> None:
    data = read_stdin_json()
    session_id = str(get_session_id(data) or "").strip()
    if session_id and os.environ.get("REFLECT_POSTCOMPACT_RESET_DEDUPE") == "1":
        try:
            (state_dir() / "session-injected" / f"{session_id}.json").unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    forensics_log(_HOOK_NAME, f"session={session_id[:8] if session_id else '?'} trigger={data.get('trigger', '?')}")


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
