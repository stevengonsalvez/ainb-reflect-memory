"""The writer argv library has no side effects and the hook uses it."""

from __future__ import annotations

import re
import subprocess
import sys

from .conftest import PLUGIN, WRITER_ARGV_LIB, agentic_writer_argv, clean_env

sys.path.insert(0, str(PLUGIN / "scripts"))
import drain_extract


def test_sourcing_the_library_has_no_side_effects(home) -> None:
    """Sourced with the kill switch set: returns, defines the function, prints
    argv, installs no trap, exits nothing. Traps are counted before and after
    the source: a signal the test process ignores (a library in the pytest
    process can leave one ignored) is inherited by bash and listed by
    ``trap -p`` too, and that is not a side effect of the library."""
    script = (
        'before=$(trap -p | wc -l | tr -d " "); '
        'source "$1"; rc=$?; '
        'drain_agentic_writer_argv "p q"; '
        'printf "%s\\n" "${WRITER_ARGV[@]}"; '
        'echo "TRAPS_ADDED:$(( $(trap -p | wc -l | tr -d " ") - before ))"; '
        'echo "RC:$rc"; echo "REACHED"'
    )
    env = clean_env(home, REFLECT_DISABLED="1", REFLECT_DRAIN_MODEL="haiku")
    proc = subprocess.run(["bash", "-c", script, "lib", str(WRITER_ARGV_LIB)], env=env,
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert "REACHED" in lines and "RC:0" in lines, proc.stdout + proc.stderr
    assert "TRAPS_ADDED:0" in lines, proc.stdout + proc.stderr
    assert lines[:3] == ["claude", "-p", "p q"]
    assert not (home / ".reflect" / "drain.lock.d").exists()


def test_hook_sources_the_library_and_uses_its_argv() -> None:
    hook = (PLUGIN / "hooks" / "reflect-drain-bg.sh").read_text(encoding="utf-8")
    assert 'source "${SCRIPT_DIR}/lib/writer_argv.sh"' in hook
    assert re.search(r'drain_agentic_writer_argv "\$(?:prompt|\(drain_writer_prompt "\$prompt"\))"', hook)
    assert '"${WRITER_ARGV[@]}"' in hook
    # No second copy of the argv inline.
    assert hook.count("--permission-mode") == 0


def test_bash_and_python_writers_share_the_frame(home) -> None:
    env = clean_env(home)
    bash_argv = agentic_writer_argv("prompt", env)
    py_argv = drain_extract.writer_argv("prompt", model="sonnet")
    for argv in (bash_argv, py_argv):
        assert argv[1:3] == ["-p", "prompt"]
        assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
        assert "--max-turns" in argv


def test_hook_fails_loudly_when_the_library_is_missing(home, tmp_path) -> None:
    """The hook runs under set -u without -e; a silently failed source would
    reach an unset WRITER_ARGV expansion much later. It must stop at once."""
    import shutil

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy(PLUGIN / "hooks" / "reflect-drain-bg.sh", hooks / "reflect-drain-bg.sh")
    env = clean_env(home)
    env.pop("REFLECT_DISABLED", None)
    proc = subprocess.run(["bash", str(hooks / "reflect-drain-bg.sh")], env=env,
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 1
    assert "missing" in proc.stderr and "writer_argv.sh" in proc.stderr


def test_empty_argv_values_survive_extraction(tmp_path) -> None:
    """The extraction helper must keep "" elements. The live scenarios hand the
    argv to the CLI verbatim, so a dropped "" would shift every following flag
    and the test would exercise a different command line than the hook runs."""
    from .conftest import agentic_writer_argv

    lib = tmp_path / "lib.sh"
    lib.write_text('drain_agentic_writer_argv() { WRITER_ARGV=(claude -p "$1" --setting-sources "" --max-turns 1); }\n')
    assert agentic_writer_argv("hello", {"PATH": "/usr/bin:/bin"}, lib=lib) == [
        "claude", "-p", "hello", "--setting-sources", "", "--max-turns", "1",
    ]

