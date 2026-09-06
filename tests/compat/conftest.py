# ABOUTME: The backward-compatibility gate. Captures what the merge-base with
# ABOUTME: main installs and does, captures the same for this branch, and fails
# ABOUTME: on any difference a test has not explicitly whitelisted.
"""Shared machinery for tests/compat.

There are no committed goldens. At test time the gate resolves
``git merge-base origin/main HEAD``, checks it out into a throwaway
``git worktree`` under tmp, runs ``capture.py`` against that tree and against
this checkout, and diffs the two. The ``ALLOWED_*_DIFF`` whitelists in the
test modules are the only review surface for an intended change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _support.hermetic import hermetic_env, minimal_path
from _support.pg import disposable_database

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = Path(__file__).resolve().parent / "capture.py"
WRITER_ARGV_LIB = PLUGIN / "hooks" / "lib" / "writer_argv.sh"
TRANSCRIPT = REPO / "tests" / "fixtures" / "transcripts" / "recorded-session.jsonl"

HARNESS_DIR = {"claude": ".claude", "codex": ".codex", "copilot": ".copilot", "hermes": ".hermes"}
HARNESSES = tuple(HARNESS_DIR)

# Stevie's kind of install: personal settings.json with bypassPermissions and a uv allow rule.
BYPASS_SETTINGS = {"permissions": {"defaultMode": "bypassPermissions", "allow": ["Bash(uv:*)"]}}

AllowedDiff = Callable[[str, Any, Any], bool]


# --------------------------------------------------------------------------- #
# subprocess + env helpers
# --------------------------------------------------------------------------- #


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None,
        timeout: int = 900, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=str(cwd) if cwd else None, input=stdin,
                          capture_output=True, text=True, timeout=timeout, check=False)


def clean_env(home: Path, **extra: str) -> dict[str, str]:
    """Throwaway HOME, minimal PATH (this interpreter, uv, git, system dirs)."""
    env = hermetic_env(
        kb_dir=home / ".learnings", state_dir=home / ".reflect", cache_home=home / ".cache",
        base={}, home=home, path=minimal_path("uv", "git"),
    )
    env["REFLECT_DRAIN_NO_DELEGATE"] = "1"
    env["REFLECT_DISABLED"] = "1"  # sourcing the argv library must never drain
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def git_repo(path: Path, message: str = "fix jwt auth token expiry check") -> Path:
    """A one-commit git repo (the recall hook derives its query from it)."""
    path.mkdir(parents=True, exist_ok=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "--initial-branch=main"], cwd=path, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", message], cwd=path, check=True)
    return path


# --------------------------------------------------------------------------- #
# writer argv: the hook's library is the single source of truth
# --------------------------------------------------------------------------- #


def agentic_writer_argv(prompt: str, env: dict[str, str], lib: Path = WRITER_ARGV_LIB) -> list[str]:
    """Source hooks/lib/writer_argv.sh and read back WRITER_ARGV."""
    script = (
        'source "$1" || { echo "source failed" >&2; exit 97; }; '
        'declare -F drain_agentic_writer_argv >/dev/null || { echo "no drain_agentic_writer_argv" >&2; exit 98; }; '
        'drain_agentic_writer_argv "$2"; '
        '[ "${#WRITER_ARGV[@]}" -gt 0 ] || { echo "WRITER_ARGV empty" >&2; exit 99; }; '
        'printf "%s\\0" "${WRITER_ARGV[@]}"'
    )
    proc = subprocess.run(["bash", "-c", script, "argv", str(lib), prompt],
                          env=env, capture_output=True, timeout=60, check=False)
    if proc.returncode != 0:
        pytest.fail(f"could not read writer argv from {lib} (exit {proc.returncode}): "
                    f"{proc.stderr.decode(errors='replace')}")
    parts = proc.stdout.split(b"\0")
    assert parts[-1] == b"", "argv stream must end with a NUL terminator"
    # Keep empty elements: a flag whose value is "" (for example
    # --setting-sources "") must reach the CLI as an empty argument, and a
    # test that dropped it would silently test a different command line.
    return [p.decode("utf-8") for p in parts[:-1]]


def replace_flag_value(argv: list[str], flag: str, value: str) -> list[str]:
    """argv with the value after ``flag`` replaced; the flag itself untouched."""
    return [value if i > 0 and argv[i - 1] == flag else a for i, a in enumerate(argv)]


def flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i] for i in range(1, len(argv)) if argv[i - 1] == flag]


def permission_rules(argv: list[str]) -> list[str]:
    """Every permission rule the argv grants: --allowedTools values plus the
    permissions.allow list of any --settings file or inline JSON."""
    rules: list[str] = []
    for value in flag_values(argv, "--allowedTools") + flag_values(argv, "--allowed-tools"):
        rules.extend(r.strip() for r in value.split(",") if r.strip())
    for value in flag_values(argv, "--settings"):
        # Inline JSON starts with "{"; anything else is a settings file path.
        # (Path.exists on a long inline document raises ENAMETOOLONG.)
        text = value if value.lstrip().startswith("{") else Path(value).read_text(encoding="utf-8")
        rules.extend(json.loads(text).get("permissions", {}).get("allow", []) or [])
    return rules


def absolute_paths_in_rules(rules: list[str]) -> set[str]:
    """Absolute paths named inside permission rules (Bash prefixes, Write/Edit scopes)."""
    import re

    paths: set[str] = set()
    for rule in rules:
        inner = rule[rule.find("(") + 1 : rule.rfind(")")] if "(" in rule else ""
        for m in re.finditer(r"(/[^\s:*)\"',]+)", inner):
            paths.add(m.group(1).rstrip("/"))
    return paths


# --------------------------------------------------------------------------- #
# baseline worktree + captures
# --------------------------------------------------------------------------- #


def _git(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def merge_base_with_main() -> str:
    """The commit the gate compares against: merge-base(origin/main, HEAD)."""
    if _git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        fetched = _git("fetch", "--quiet", "origin", "main:refs/remotes/origin/main")
        if fetched.returncode != 0:
            pytest.skip(f"origin/main not available for the baseline: {fetched.stderr.strip()}")
    proc = _git("merge-base", "origin/main", "HEAD")
    if proc.returncode != 0:
        pytest.skip(f"no merge-base with origin/main: {proc.stderr.strip()}")
    return proc.stdout.strip()


@pytest.fixture(scope="session")
def baseline_tree(tmp_path_factory) -> Path:
    """A throwaway worktree at the merge-base with main."""
    sha = merge_base_with_main()
    root = tmp_path_factory.mktemp("baseline") / "tree"
    proc = _git("worktree", "add", "--detach", "--quiet", str(root), sha)
    if proc.returncode != 0:
        pytest.skip(f"could not create the baseline worktree: {proc.stderr.strip()}")
    try:
        yield root
    finally:
        _git("worktree", "remove", "--force", str(root))


def capture(kind: str, *, tree: Path, home: Path) -> dict[str, Any]:
    """Run capture.py against ``tree`` with a fresh ``home``; parsed JSON."""
    home.mkdir(parents=True, exist_ok=True)
    proc = run([sys.executable, str(CAPTURE), "--tree", str(tree), "--home", str(home),
                "--kind", kind, "--fixtures", str(FIXTURES)],
               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=3600)
    if proc.returncode != 0:
        pytest.fail(f"capture {kind} on {tree} failed (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


_CAPTURES: dict[tuple[str, str], dict[str, Any]] = {}


@pytest.fixture(scope="session")
def captures(baseline_tree: Path, tmp_path_factory):
    """``captures(kind)`` -> (baseline, branch); each side captured once per session."""

    def _get(kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for label, tree in (("baseline", baseline_tree), ("branch", REPO)):
            if (kind, label) not in _CAPTURES:
                home = tmp_path_factory.mktemp(f"{kind}-{label}") / "home"
                _CAPTURES[(kind, label)] = capture(kind, tree=tree, home=home)
        return _CAPTURES[(kind, "baseline")], _CAPTURES[(kind, "branch")]

    return _get


# --------------------------------------------------------------------------- #
# diffing with a whitelist
# --------------------------------------------------------------------------- #


def flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    if isinstance(node, list):
        out = {}
        for i, v in enumerate(node):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: node}


def refused_diffs(baseline: Any, branch: Any, allowed: AllowedDiff | None = None) -> list[tuple[str, Any, Any]]:
    b, c = flatten(baseline), flatten(branch)
    refused = []
    for key in sorted(set(b) | set(c)):
        old, new = b.get(key, "<absent>"), c.get(key, "<absent>")
        if old != new and not (allowed and allowed(key, old, new)):
            refused.append((key, old, new))
    return refused


def _describe(key: str, old: Any, new: Any) -> str:
    if isinstance(old, str) and isinstance(new, str) and ("\n" in old or "\n" in new):
        import difflib

        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "baseline", "branch", lineterm="", n=1))
        return f"{key}:\n    " + "\n    ".join(diff[:30])
    return f"{key}: {str(old)[:160]!r} -> {str(new)[:160]!r}"


def assert_same_as_baseline(name: str, baseline: Any, branch: Any, *, allowed: AllowedDiff | None = None) -> None:
    refused = refused_diffs(baseline, branch, allowed)
    if refused:
        lines = [_describe(k, o, n) for k, o, n in refused[:40]]
        pytest.fail(f"{name}: {len(refused)} unwhitelisted difference(s) vs the merge-base baseline:\n  "
                    + "\n  ".join(lines))


def any_of(*predicates: AllowedDiff) -> AllowedDiff:
    return lambda key, old, new: any(p(key, old, new) for p in predicates)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def disposable_pg():
    """DSN of a reflect_test_<random> database created for this session on the
    localhost server, dropped afterwards. Skips without a local server."""
    with disposable_database() as dsn:
        yield dsn


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".learnings" / "documents").mkdir(parents=True)
    (h / ".reflect").mkdir()
    return h


@pytest.fixture
def env(home: Path) -> dict[str, str]:
    return clean_env(home)


@pytest.fixture
def reflect_bin() -> Path:
    bin_ = Path(sys.executable).parent / "reflect"
    if not bin_.exists():
        pytest.skip(f"reflect CLI not installed next to {sys.executable}")
    return bin_


def install_adapter(harness: str, home: Path) -> Path:
    """Run this checkout's adapter install into ``home``; return the harness dir."""
    adapter = PLUGIN / "adapters" / harness / f"{harness}_adapter.py"
    proc = run([sys.executable, str(adapter), "install", "--home", str(home)], env=clean_env(home))
    assert proc.returncode == 0, f"{harness} install failed:\n{proc.stdout}\n{proc.stderr}"
    return home / HARNESS_DIR[harness]


def live_mode() -> str | None:
    """'key' with ANTHROPIC_API_KEY (CI), 'operator' when the operator opts in
    to spend on their own login, else None."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "key"
    if os.environ.get("REFLECT_COMPAT_LIVE") == "operator":
        return "operator"
    return None


def require_live() -> str:
    mode = live_mode()
    if mode is None:
        pytest.skip("live: set ANTHROPIC_API_KEY or REFLECT_COMPAT_LIVE=operator")
    if not shutil.which("claude"):
        pytest.skip("live: claude CLI not on PATH")
    return mode
