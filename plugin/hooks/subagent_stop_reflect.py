#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SubagentStop reflection queue producer.

Queues subagent transcripts for the existing background drain. This hook does
not run `/reflect`; the SessionStart drain remains the sole consumer.
"""

from __future__ import annotations

import json
import sys
import traceback

from hook_common import (  # noqa: E402
    enqueue_reflection,
    forensics_log,
    get_agent_id,
    get_agent_transcript_path,
    get_agent_type,
    get_parent_session_id,
    get_session_id,
    get_transcript_path,
    read_stdin_json,
    write_last_event,
)


_HOOK_NAME = "subagent_stop_reflect"


def _main_body() -> None:
    data = read_stdin_json()
    transcript = str(get_agent_transcript_path(data) or get_transcript_path(data) or "").strip()
    queued = enqueue_reflection(
        trigger="subagent_stop",
        data=data,
        transcript_path=transcript,
        scope="subagent",
        dedupe_session=False,
        extra={
            "agent_id": get_agent_id(data),
            "agent_type": get_agent_type(data),
            "parent_session_id": get_parent_session_id(data) or get_session_id(data),
        },
    )
    forensics_log(_HOOK_NAME, f"queued={queued} agent={get_agent_type(data) or '?'}")
    # Codex documents JSON stdout for SubagentStop. Empty object means no control decision.
    print(json.dumps({}))


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
        print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
