#!/usr/bin/env python3
# ABOUTME: PreToolUse hook for the drain's headless writer: decides every Bash
# ABOUTME: call before it runs, on one normalised command, with a reason.
"""The drain's writer runs ``claude -p`` under an inline ``--settings``
document (hooks/lib/writer_argv.sh) that carries the allow rules and this
hook. A rule matches a command prefix as spelled, so ``cd ~ && reflect add
x`` or ``env FOO=1 reflect skill-step ...`` was denied after the fact and the
run was classified. This hook decides before the call instead: it strips
the shell noise a model adds (a leading ``cd <dir> &&``, ``env`` and
``NAME=value`` prefixes, ``exec``) and allows the call when what remains is
one of the drain's commands, else denies it with a reason that names the
surface, so the writer's next attempt is the plain command.

Other tools produce no decision here; the rules in the same document decide
them. Reads the PreToolUse JSON on stdin, prints one decision, exits 0 (a
crash prints nothing, and the rules then apply). Stdlib only.
"""

from __future__ import annotations

import json
import shlex
import sys

ALLOWED = (("reflect", "skill-step"), ("reflect", "add"), ("reflect", "search"))
SURFACE = ("only `reflect skill-step <step> ...`, `reflect add ...` and `reflect search ...` may run "
           "here, as one plain command: no pipes or `;`, no python3, no uv run, no shell variables. "
           "Every step of the skill is a `reflect skill-step` command.")
_OPERATORS = {"&&", "||", "|", ";", "&", ">", ">>", "<", "(", ")"}


def normalise(command: str) -> list[str] | None:
    """The argv the command runs once the noise is gone, or None when it is
    not one plain command (a pipe, a second command, a redirect)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _OPERATORS or (tok and set(tok) <= set("&|;<>()")):
            if tok != "&&":
                return None
            segments.append([])
        else:
            segments[-1].append(tok)
    *leading, last = segments
    for seg in leading:  # only `cd <dir>` may precede the command
        if len(seg) != 2 or seg[0] != "cd":
            return None
    argv = list(last)
    while argv:
        head = argv[0]
        if head in ("env", "exec", "command") or ("=" in head and not head.startswith("=")
                                                    and head.split("=", 1)[0].replace("_", "").isalnum()):
            argv = argv[1:]
            continue
        break
    return argv or None


def decide(data: dict) -> dict | None:
    if data.get("tool_name") != "Bash":
        return None
    command = str((data.get("tool_input") or {}).get("command") or "")
    argv = normalise(command)
    if argv and any(tuple(argv[: len(prefix)]) == prefix for prefix in ALLOWED):
        return {"permissionDecision": "allow",
                "permissionDecisionReason": f"drain writer: `{' '.join(argv[:2])}` is a granted command"}
    return {"permissionDecision": "deny", "permissionDecisionReason": "drain writer: " + SURFACE}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    decision = decide(data) if isinstance(data, dict) else None
    if decision:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook crash must never block the writer
        sys.exit(0)
