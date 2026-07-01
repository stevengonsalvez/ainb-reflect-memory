#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse narrow policy/context hook.

This hook intentionally avoids broad recall. It consults deterministic policy
rules from `REFLECT_POLICY_FILE` or `~/.reflect/policy-rules.jsonl` and either
adds small model-visible context or denies exact high-confidence deny rules.
"""

from __future__ import annotations

import sys
import traceback

from hook_common import (  # noqa: E402
    emit_additional_context,
    emit_pretool_deny,
    forensics_log,
    get_tool_name,
    high_confidence,
    matching_policy_rules,
    read_stdin_json,
    scrub_secrets,
    write_last_event,
)


_HOOK_NAME = "pretooluse_context"
_EVENT_NAME = "PreToolUse"


def _main_body() -> None:
    data = read_stdin_json()
    rules = matching_policy_rules(data, scope="pretool")
    for rule in rules:
        if str(rule.get("decision", "")).lower() == "deny" and high_confidence(rule):
            message = scrub_secrets(str(rule.get("message") or "Blocked by Reflect policy."))
            emit_pretool_deny(message)
            forensics_log(_HOOK_NAME, f"deny tool={get_tool_name(data) or '?'}")
            return

    contexts = [
        scrub_secrets(str(rule.get("context") or rule.get("message") or "")).strip()
        for rule in rules
        if str(rule.get("decision", "context")).lower() in ("context", "allow", "")
        and high_confidence(rule)
    ]
    contexts = [c for c in contexts if c]
    if contexts:
        emit_additional_context(_EVENT_NAME, "Reflect tool context:\n" + "\n".join(f"- {c}" for c in contexts[:3]))
        return
    # Plain text is ignored for PreToolUse; no output means no decision.


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
