"""Every `claude -p` call site in the tree is known, and each one either
carries the shared no-tools flags (a tool-free child) or is the drain's
agentic writer under its pinned permission surface. A new call site fails
this test until it is added here with its reason."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PLUGIN = _REPO / "plugin"
sys.path.insert(0, str(_PLUGIN / "scripts"))
sys.path.insert(0, str(_PLUGIN / "skills" / "recall" / "scripts"))
sys.path.insert(0, str(_REPO / "src"))
import drain_extract
import reflect_synthesis
from reflect_kb.issues import analyze

# path -> how the child is contained
CALL_SITES = {
    "plugin/scripts/drain_extract.py": "tool-free: NO_TOOLS_FLAGS, nested",
    "plugin/skills/recall/scripts/recall.py": "tool-free: the same flags inline (stdlib skill script), nested",
    "plugin/scripts/reflect_synthesis.py": "tool-free: NO_TOOLS_FLAGS, nested",
    "src/reflect_kb/issues/analyze.py": "tool-free: NO_TOOLS_FLAGS, nested",
    "plugin/hooks/lib/writer_argv.sh": "agentic writer: permission-mode default, hook-owned rules and guard, no MCP, nested",
}
# A call: a python argv literal ("claude", "-p" / claude_bin, "-p"), the bash
# argv array's -p "$prompt", or a bare `claude -p` on a line that is code
# (not a comment, a log line, an error message or a backticked mention).
_PY_CALL = re.compile(r'(?:"claude"|\bclaude_bin)\s*,\s*"-p"')
_SH_CALL = re.compile(r'^\s*-p "\$prompt"|(?<![`\w])claude -p(?![`\w])')
_NOT_CODE = ("#", "log ", "log(", "emit_error ", "echo ", "print(", "*", "//", '"""', "'" * 3)


def _sources():
    for root in (_PLUGIN, _REPO / "src"):
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".sh") or "tests" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _is_call(line: str) -> bool:
    line = line.split(" #", 1)[0]  # a trailing comment is not code
    stripped = line.strip()
    if stripped.startswith(_NOT_CODE) or "`claude -p`" in line or "help=" in line:
        return False
    return bool(_PY_CALL.search(line) or _SH_CALL.search(line))


def test_every_call_site_is_listed() -> None:
    found = set()
    for path in _sources():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not _is_call(line):
                continue
            if "\n".join(lines[:i]).count('"""') % 2 == 1:  # inside a docstring
                continue
            found.add(path.relative_to(_REPO).as_posix())
    assert found == set(CALL_SITES), f"call sites changed: {sorted(found ^ set(CALL_SITES))}"


def test_tool_free_children_share_the_flags() -> None:
    assert tuple(drain_extract.NO_TOOLS_FLAGS) == tuple(analyze.NO_TOOLS_FLAGS)
    flags = list(drain_extract.NO_TOOLS_FLAGS)
    for argv in (drain_extract.writer_argv("p", model="haiku"), analyze.analyzer_argv("p", "haiku"),
                 reflect_synthesis.synthesis_argv("p", "opus")):
        assert argv[1] == "-p" and all(f in argv for f in flags), argv
        assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--max-turns") + 1] == "1"
    import recall

    hyde = recall.hyde_argv("q", "haiku", "sys")
    assert all(f in hyde for f in flags) and hyde[hyde.index("--tools") + 1] == ""
