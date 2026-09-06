"""The writer argv library has no side effects and the hook uses it."""

from __future__ import annotations

import subprocess
import sys

from .conftest import PLUGIN, WRITER_ARGV_LIB, agentic_writer_argv, clean_env

sys.path.insert(0, str(PLUGIN / "scripts"))
import drain_extract


def test_sourcing_the_library_has_no_side_effects(home) -> None:
    """Sourced with the kill switch set: returns, defines the function, prints
    argv, installs no trap, exits nothing."""
    script = (
        'source "$1"; rc=$?; '
        'drain_agentic_writer_argv "p q"; '
        'printf "%s\\n" "${WRITER_ARGV[@]}"; '
        'echo "TRAPS:$(trap -p | wc -l | tr -d " ")"; '
        'echo "RC:$rc"; echo "REACHED"'
    )
    env = clean_env(home, REFLECT_DISABLED="1", REFLECT_DRAIN_MODEL="haiku")
    proc = subprocess.run(["bash", "-c", script, "lib", str(WRITER_ARGV_LIB)], env=env,
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert "REACHED" in lines and "RC:0" in lines
    assert "TRAPS:0" in lines
    assert lines[:3] == ["claude", "-p", "p q"]
    assert not (home / ".reflect" / "drain.lock.d").exists()


def test_hook_sources_the_library_and_uses_its_argv() -> None:
    hook = (PLUGIN / "hooks" / "reflect-drain-bg.sh").read_text(encoding="utf-8")
    assert 'source "${SCRIPT_DIR}/lib/writer_argv.sh"' in hook
    assert 'drain_agentic_writer_argv "$prompt"' in hook
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
