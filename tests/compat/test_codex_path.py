"""Gate 5: the Codex path.

Install through this checkout's codex adapter into a throwaway ~/.codex and
read the installed SKILL.md exactly as the adapter wrote it (no substitution
in the test). Then run every python and reflect command the reflect skill
tells the model to run, as literal commands from that layout; each exits 0.
``codex exec`` is optional and needs a key.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from .conftest import FIXTURES, REPO, TRANSCRIPT, clean_env, install_adapter, run

FIXTURE_DOC = FIXTURES / "compat-note.md"
FIXTURE_SIDECAR = FIXTURES / "compat-note.entities.yaml"

_CMD_RE = re.compile(r"^\s*(?:uv run|python3?|reflect)\s+\S", re.MULTILINE)
_SCRIPT_RE = re.compile(r"(?:python3?|uv run)\s+\"?([^\s\"]+\.py)\"?")


@pytest.fixture
def codex_home(home: Path) -> Path:
    install_adapter("codex", home)
    return home


def _installed_skill(codex_home: Path) -> str:
    return (codex_home / ".codex" / "skills" / "reflect" / "SKILL.md").read_text(encoding="utf-8")


def test_installed_skill_is_rendered_and_names_existing_scripts(codex_home: Path) -> None:
    text = _installed_skill(codex_home)
    assert "{{HOME_TOOL_DIR}}" not in text, "adapter left the bootstrap placeholder in the skill"
    scripts = sorted({m.group(1) for m in _SCRIPT_RE.finditer(text) if m.group(1).startswith("/")})
    assert scripts, "the skill should name at least one script by absolute path"
    missing = [s for s in scripts if not Path(s).exists()]
    assert not missing, f"skill commands point at scripts missing from ~/.codex: {missing}"
    for m in re.finditer(r'^\s*VALIDATE="([^"]+)"', text, re.MULTILINE):
        assert Path(m.group(1)).exists(), m.group(1)


def test_every_skill_command_runs_from_the_codex_layout(codex_home: Path, reflect_bin: Path) -> None:
    text = _installed_skill(codex_home)
    assert _CMD_RE.search(text), "skill emits no python/reflect commands?"
    scripts = codex_home / ".codex" / "skills" / "reflect" / "scripts"
    env = clean_env(codex_home)
    py = sys.executable
    commands: list[list[str]] = [
        # uv run "$VALIDATE" --strict "$SIDECAR": run with this interpreter
        # (its only dependency, pyyaml, is installed here; uv would build a
        # fresh env over the network).
        [py, str(scripts / "validate_sidecar.py"), "--strict", str(FIXTURE_SIDECAR)],
        [str(reflect_bin), "add", str(FIXTURE_DOC), "--entities", str(FIXTURE_SIDECAR), "--force"],
        [py, str(scripts / "metrics_updater.py"), "--accepted", "1", "--rejected", "0",
         "--confidence", "high:1,medium:0,low:0", "--agents", "compat", "--skills", "0"],
        [py, str(scripts / "state_manager.py"), "status"],
        [py, str(scripts / "state_manager.py"), "on"],
        [py, str(scripts / "state_manager.py"), "off"],
        [py, str(scripts / "reflect_cascade.py"), "revise", "--source", str(TRANSCRIPT), "--actions", "[]"],
        [py, str(scripts / "reflect_cascade.py"), "observe", "--actions", "[]"],
    ]
    for script in sorted({m.group(1) for m in _SCRIPT_RE.finditer(text) if m.group(1).startswith("/")}):
        commands.append([py, script, "--help"])
    failures = []
    for cmd in commands:
        proc = run(cmd, env=env, cwd=codex_home, timeout=600)
        if proc.returncode != 0:
            failures.append(f"exit {proc.returncode}: {' '.join(cmd)}\n{proc.stderr[-800:]}")
    assert not failures, "\n\n".join(failures)
    assert list((codex_home / ".learnings" / "documents").glob("*.md")), "reflect add wrote nothing into ~/.learnings"


@pytest.mark.live
def test_codex_exec_smoke(codex_home: Path) -> None:
    """Optional: codex must start with the installed skill. Needs a key and the CLI."""
    if not shutil.which("codex") or not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("codex CLI or OPENAI_API_KEY not available")
    proc = run(["codex", "exec", "--skip-git-repo-check", "Reply with the word PONG only."],
               env=clean_env(codex_home, OPENAI_API_KEY=os.environ["OPENAI_API_KEY"]), timeout=180)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "PONG" in proc.stdout.upper()
    assert REPO.is_dir()
