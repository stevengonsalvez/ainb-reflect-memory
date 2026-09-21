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


def test_installed_skill_is_rendered_and_names_no_script_path(codex_home: Path) -> None:
    """Every scripted step is `reflect skill-step <step>`; the rendered skill
    names no script by path (the reflect CLI resolves the codex layout's
    scripts itself), and any resource it does name by absolute path exists."""
    text = _installed_skill(codex_home)
    assert "{{HOME_TOOL_DIR}}" not in text, "adapter left the bootstrap placeholder in the skill"
    scripts = sorted({m.group(1) for m in _SCRIPT_RE.finditer(text)})
    assert not scripts, f"the skill still names scripts by path: {scripts}"
    assert set(re.findall(r"reflect skill-step (\S+)", text)) == {"index", "metrics", "state", "revise", "observe"}
    named = {m.group(0) for m in re.finditer(re.escape(str(codex_home)) + r"/[^\s`'\")]+", text)}
    missing = [p for p in named if not Path(p).exists()]
    assert not missing, f"the skill names paths missing from ~/.codex: {missing}"


def test_every_skill_command_runs_from_the_codex_layout(codex_home: Path, reflect_bin: Path) -> None:
    """Every command the installed skill spells runs from the codex layout:
    `reflect skill-step <step>` resolves the layout's scripts through
    REFLECT_SKILL_SCRIPTS_DIR (what the drain exports) and the plain
    `reflect add` indexes into ~/.learnings."""
    text = _installed_skill(codex_home)
    assert _CMD_RE.search(text), "skill emits no reflect commands?"
    scripts = codex_home / ".codex" / "skills" / "reflect" / "scripts"
    # PYTHONPATH pins the reflect CLI to this checkout, whatever the interpreter has installed
    env = clean_env(codex_home, REFLECT_SKILL_SCRIPTS_DIR=str(scripts), PYTHONPATH=str(REPO / "src"))
    reflect = str(reflect_bin)
    commands: list[list[str]] = [
        [reflect, "skill-step", "validate-sidecar", "--strict", str(FIXTURE_SIDECAR)],
        [reflect, "skill-step", "index", str(FIXTURE_DOC), str(FIXTURE_SIDECAR)],
        [reflect, "add", str(FIXTURE_DOC), "--entities", str(FIXTURE_SIDECAR), "--force"],
        [reflect, "skill-step", "metrics", "--accepted", "1", "--rejected", "0",
         "--confidence", "high:1,medium:0,low:0", "--agents", "compat", "--skills", "0"],
        [reflect, "skill-step", "state", "status"],
        [reflect, "skill-step", "state", "on"],
        [reflect, "skill-step", "state", "off"],
        [reflect, "skill-step", "revise", "--source", str(TRANSCRIPT), "--actions", "[]"],
        [reflect, "skill-step", "observe", "--actions", "[]"],
    ]
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
