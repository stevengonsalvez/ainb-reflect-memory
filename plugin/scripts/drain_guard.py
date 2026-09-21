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

Other tools are denied when they touch a credential path (``~/.ssh``, cloud
and forge credentials, ``.env``, keys) or when an Edit or Write would change
the frontmatter of a skill or agent file: an injected transcript must not
read the operator's secrets, nor plant ``hooks:`` or ``permissionMode:`` in a
skill an interactive session later loads. Every other tool call produces no
decision here; the rules in the same document decide it. Reads the PreToolUse JSON on stdin, prints one decision, exits 0 (a
crash prints nothing, and the rules then apply). Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

DEFAULT_ALLOWED = (("reflect", "skill-step"), ("reflect", "add"), ("reflect", "search"))
SURFACE = ("only `reflect skill-step <step> ...`, `reflect add ...` and `reflect search ...` may run "
           "here, as one plain command: no pipes or `;`, no python3, no uv run, no shell variables, "
           "no env prefixes, and notes only under docs/solutions/. "
           "Every step of the skill is a `reflect skill-step` command.")
_OPERATORS = set("&|;<>()")
_FRONTMATTER_KEY_RE = re.compile(r"^(name|description|hooks|allowed-tools|tools|model|"
                                 r"permission-?mode|disable-model-invocation|argument-hint):", re.I)
_NOTE_ROOT = os.path.join("docs", "solutions")
_PATH_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep")
_SEARCH_TOOLS = ("Glob", "Grep")
# Where a search may run. A Read names one file and the rules scope it, but a
# search walks a tree and the harness judges a read-deny rule on the search
# root alone, so a search outside these roots is denied outright.
_SEARCH_ROOTS = ("docs/solutions", "~/.reflect", "~/.learnings",
                 "~/.claude/skills", "~/.claude/agents")
# Credential stores the writer has no business in. Matched on the resolved
# path, so a symlink or a ../ walk into one is caught too.
_DENIED_DIRS = ("/.ssh", "/.aws", "/.gnupg", "/.config/gcloud", "/.config/gh",
                "/.kube", "/.docker", "/.gem", "/.azure", "/.password-store",
                "/.claude/projects")
_DENIED_NAMES = (".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
                 ".credentials.json", "credentials", "id_rsa", "id_ed25519",
                 "id_ecdsa", "id_dsa", ".pgpass", "secrets.yaml", "secrets.yml")
_DENIED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx", ".keychain")
_FRONTMATTER_SCOPES = (("/.claude/skills/",), ("/.claude/agents/",),
                       ("/.codex/skills/",), ("/.copilot/skills/",))
_SEARCH_SURFACE = ("a search may only run under the drain's own trees: docs/solutions, ~/.reflect, "
                   "~/.learnings, ~/.claude/skills, ~/.claude/agents. Name the file to read it")
_CREDENTIAL_SURFACE = ("credential paths are out of scope for the drain writer: no ~/.ssh, cloud or "
                       "forge credentials, .env files, keys or raw transcripts")
_FRONTMATTER_SURFACE = ("a skill or agent file's frontmatter is out of scope for the drain writer: "
                        "edit the body only, never the `---` block (hooks, allowed-tools, "
                        "permissionMode)")


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


def _resolve(path: str, cwd: str) -> str:
    return os.path.realpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)))


def is_credential_path(path: str, cwd: str) -> bool:
    """True when the path names a credential store, by directory, file name or
    extension, after symlinks and ``..`` are resolved."""
    if not path:
        return False
    real = _resolve(path, cwd)
    name = os.path.basename(real)
    return (any(d + "/" in real + "/" for d in _DENIED_DIRS)
            or name in _DENIED_NAMES
            or name.startswith(".env")
            or name.endswith(_DENIED_SUFFIXES))


def _frontmatter_span(path: str) -> str | None:
    """The text of the file's leading ``---`` block, or None when the file
    cannot be read or opens with no block."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(65536)
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    return text[:end + 4] if end > 0 else None


def _search_roots(cwd: str) -> list[str]:
    roots = [os.environ.get("GLOBAL_LEARNINGS_PATH", ""), *_SEARCH_ROOTS]
    return [_resolve(r, cwd) for r in roots if r]


def search_is_scoped(tool_input: dict, cwd: str) -> bool:
    """True when a Glob or Grep runs under one of the drain's own trees. The
    search root is the ``path`` argument, or the directory part of an absolute
    Glob pattern; a search with neither walks the writer's whole home."""
    root = str(tool_input.get("path") or "")
    if not root:
        pattern = str(tool_input.get("pattern") or "")
        if not pattern.startswith(("/", "~")):
            return False
        root = pattern.split("*", 1)[0]
    real = _resolve(root, cwd)
    return any(real == allowed or real.startswith(allowed + os.sep) for allowed in _search_roots(cwd))


def touches_frontmatter(tool: str, tool_input: dict, cwd: str) -> bool:
    """True when the call would change the `---` block of a skill or agent
    file: an edit whose old or new text carries a delimiter line or a
    frontmatter key, or a whole-file Write."""
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not path:
        return False
    real = _resolve(path, cwd)
    if not any(scope in real for scopes in _FRONTMATTER_SCOPES for scope in scopes):
        return False
    if tool in ("Write", "NotebookEdit"):
        return True  # a whole-file write replaces the frontmatter with it
    edits = tool_input.get("edits") or [tool_input]
    block = _frontmatter_span(real)
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        old, new = str(edit.get("old_string") or ""), str(edit.get("new_string") or "")
        if block is not None:
            # The file's own `---` block decides: an edit anchored inside it
            # rewrites frontmatter, one below it cannot, whatever it contains.
            # A horizontal rule in the body is body text, not a delimiter.
            if old and old in block:
                return True
            continue
        for text in (old, new):  # unreadable file: judge the text alone
            for line in text.splitlines():
                stripped = line.strip()
                if stripped == "---" or _FRONTMATTER_KEY_RE.match(stripped):
                    return True
    return False


def decide(data: dict, allowed: tuple[tuple[str, ...], ...] = DEFAULT_ALLOWED) -> dict | None:
    tool = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    cwd = str(data.get("cwd") or "")
    if tool in _PATH_TOOLS:
        if not isinstance(tool_input, dict):
            return None
        keys = ("file_path", "notebook_path", "path", "glob") if tool in _SEARCH_TOOLS \
            else ("file_path", "notebook_path", "path")
        for key in keys:
            if is_credential_path(str(tool_input.get(key) or ""), cwd):
                return {"permissionDecision": "deny",
                        "permissionDecisionReason": "drain writer: " + _CREDENTIAL_SURFACE}
        if tool in _SEARCH_TOOLS and not search_is_scoped(tool_input, cwd):
            return {"permissionDecision": "deny",
                    "permissionDecisionReason": "drain writer: " + _SEARCH_SURFACE}
        if touches_frontmatter(tool, tool_input, cwd):
            return {"permissionDecision": "deny",
                    "permissionDecisionReason": "drain writer: " + _FRONTMATTER_SURFACE}
        return None  # the rules decide every other path
    if tool != "Bash":
        return None
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    argv = normalise(command, cwd)
    if (argv and any(tuple(argv[: len(prefix)]) == prefix for prefix in allowed)
            and _note_paths_ok(argv, cwd)):
        return {"permissionDecision": "allow",
                "permissionDecisionReason": f"drain writer: `{' '.join(argv[:2])}` is a granted command"}
    return {"permissionDecision": "deny", "permissionDecisionReason": "drain writer: " + SURFACE}


def parse_allowed(args: list[str]) -> tuple[tuple[str, ...], ...]:
    """``--allow "reflect add"`` repeated; ``--no-bash`` allows no command at
    all (the rules granted none); neither given means the defaults."""
    if "--no-bash" in args:
        return ()
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
