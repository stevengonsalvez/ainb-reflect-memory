#!/usr/bin/env python3
# ABOUTME: PreToolUse hook for the drain's headless writer: decides every Bash
# ABOUTME: call before it runs, on one plain command, with a reason.
"""The drain's writer runs ``claude -p`` under an inline ``--settings``
document (hooks/lib/writer_argv.sh) that carries the allow rules and this
hook. A hook ``allow`` skips the permission rules, so this hook is the gate
for Bash and allows only what it can prove is one plain drain command.

Allowed: ``reflect skill-step ...``, ``reflect add ...`` and ``reflect
search ...`` (or only the prefixes passed as ``--allow "reflect add"``,
which writer_argv.sh derives from the ``Bash(...)`` rules, so an operator
override that drops a prefix drops it here too), optionally after one
``cd <home or cwd> &&``, the shell noise a model adds.

Denied, before any tokenising: anything bash would expand or treat as a
second line (``$``, a backtick, a backslash other than a line continuation,
an unquoted newline or carriage return), a leading ``NAME=value``, ``env``,
``exec`` or ``command`` (the drain exports every variable the writer needs;
a prefix such as ``PYTHONPATH=docs/solutions`` would load code the writer
may write), pipes, redirects and second commands. ``reflect add`` and
``reflect skill-step index`` must name files under ``docs/solutions/`` of
the writer's cwd, so the shared store only ever receives the writer's notes.

Other tools produce no decision here; the rules in the same document decide
them. Reads the PreToolUse JSON on stdin, prints one decision, exits 0 (a
crash prints nothing, and the rules then apply). Stdlib only.
"""

from __future__ import annotations

import json
import os
import shlex
import sys

DEFAULT_ALLOWED = (("reflect", "skill-step"), ("reflect", "add"), ("reflect", "search"))
SURFACE = ("only `reflect skill-step <step> ...`, `reflect add ...` and `reflect search ...` may run "
           "here, as one plain command: no pipes or `;`, no python3, no uv run, no shell variables, "
           "no env prefixes, and notes only under docs/solutions/. "
           "Every step of the skill is a `reflect skill-step` command.")
_OPERATORS = set("&|;<>()")
_NOTE_ROOT = os.path.join("docs", "solutions")


def unsafe_shell(command: str) -> bool:
    """True when bash would expand something or start a second line: ``$``
    or a backtick outside single quotes, a backslash that is not a line
    continuation, or, outside quotes, a newline or a brace, glob or tilde
    character (so the path checked is exactly the path bash passes on)."""
    command = command.replace("\\\n", "")  # a line continuation joins the line, as bash does
    quote = ""
    for ch in command:
        if quote == "'":
            if ch == "'":
                quote = ""
            continue
        if ch in "$`\\\r":
            return True
        if quote == '"':
            if ch == '"':
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "\n{}*?[~":  # a second line, or brace, glob or tilde expansion
            return True
    return False


def normalise(command: str, cwd: str = "") -> list[str] | None:
    """The argv the command runs, or None when it is not one plain command."""
    home = os.path.expanduser("~")
    # `cd ~ &&` is the one tilde a model adds: spell it as the home before the shell check.
    if command.startswith("cd ~ && ") or command.startswith("cd ~/ && "):
        command = "cd " + shlex.quote(home) + command[command.index(" &&"):]
    if unsafe_shell(command):
        return None
    lexer = shlex.shlex(command.replace("\\\n", " "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok and set(tok) <= _OPERATORS:
            if tok != "&&":
                return None
            segments.append([])
        else:
            segments[-1].append(tok)
    *leading, argv = segments
    if len(leading) > 1:
        return None
    for seg in leading:  # only `cd <home or cwd>` may precede the command
        if len(seg) != 2 or seg[0] != "cd" or not _is_start_dir(seg[1], cwd):
            return None
    return argv or None


def _is_start_dir(target: str, cwd: str) -> bool:
    real = os.path.realpath(os.path.expanduser(target))
    return real in {os.path.realpath(os.path.expanduser("~")), os.path.realpath(cwd or os.getcwd())}


def _under_notes(path: str, cwd: str) -> bool:
    root = os.path.realpath(os.path.join(cwd or os.getcwd(), _NOTE_ROOT))
    real = os.path.realpath(os.path.join(cwd or os.getcwd(), path))
    return real.startswith(root + os.sep)


def _note_paths_ok(argv: list[str], cwd: str) -> bool:
    """``reflect add`` and ``reflect skill-step index`` name only files under
    docs/solutions/ (positional arguments and the --entities value)."""
    if argv[1] == "add":
        args = argv[2:]
    elif argv[1:3] == ["skill-step", "index"]:
        args = argv[3:]
    else:
        return True
    paths = []
    it = iter(args)
    for arg in it:
        if arg in ("--entities", "-e"):
            paths.append(next(it, ""))
        elif arg.startswith("--entities="):
            paths.append(arg.split("=", 1)[1])
        elif arg in ("--force", "-f", "--strict"):
            continue
        elif arg.startswith("-"):
            return False
        else:
            paths.append(arg)
    return bool(paths) and all(p and _under_notes(p, cwd) for p in paths)


def decide(data: dict, allowed: tuple[tuple[str, ...], ...] = DEFAULT_ALLOWED) -> dict | None:
    if data.get("tool_name") != "Bash":
        return None
    command = str((data.get("tool_input") or {}).get("command") or "")
    cwd = str(data.get("cwd") or "")
    argv = normalise(command, cwd)
    if (argv and any(tuple(argv[: len(prefix)]) == prefix for prefix in allowed)
            and _note_paths_ok(argv, cwd)):
        return {"permissionDecision": "allow",
                "permissionDecisionReason": f"drain writer: `{' '.join(argv[:2])}` is a granted command"}
    return {"permissionDecision": "deny", "permissionDecisionReason": "drain writer: " + SURFACE}


def parse_allowed(args: list[str]) -> tuple[tuple[str, ...], ...]:
    """``--allow "reflect add"`` repeated; none given means the defaults."""
    given = [tuple(args[i + 1].split()) for i, a in enumerate(args[:-1]) if a == "--allow"]
    return tuple(p for p in given if p) or DEFAULT_ALLOWED


def main(args: list[str]) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    decision = decide(data, parse_allowed(args)) if isinstance(data, dict) else None
    if decision:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001 - a hook crash must never block the writer
        sys.exit(0)
