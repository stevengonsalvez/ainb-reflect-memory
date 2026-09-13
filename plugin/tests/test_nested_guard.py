"""REFLECT_NESTED: every reflect hook exits at once in a claude that reflect
itself spawned, so the drain's writer, HyDE, the analyzer and synthesis can
keep the operator's setting sources (apiKeyHelper, the env block) without
firing a SessionStart drain or a recall of their own (recursion)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_PY_HOOKS = sorted(p for p in list((_PLUGIN / "hooks").glob("*.py")) + list((_PLUGIN / "skills" / "recall" / "hooks").glob("*.py"))
                   if p.name not in ("hook_common.py", "registry.py", "__init__.py"))
_SH_HOOKS = [_PLUGIN / "hooks" / n for n in ("reflect-drain-bg.sh", "idle_reflect.sh", "reflect-maintenance-watch.sh")]
_EVENT = {"hook_event_name": "SessionStart", "session_id": "nested", "transcript_path": "/nonexistent/t.jsonl",
          "cwd": "/", "source": "startup", "tool_name": "Bash", "tool_input": {"command": "true"},
          "prompt": "hello", "message": "x", "notification_type": "permission_prompt"}


def _run(hook: Path, tmp_path: Path) -> tuple[subprocess.CompletedProcess, set[Path]]:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    env = {**os.environ, "REFLECT_NESTED": "1", "REFLECT_STATE_DIR": str(state), "HOME": str(tmp_path),
           "GLOBAL_LEARNINGS_PATH": str(tmp_path / "kb"), "REFLECT_DRAIN_DRY_RUN": "1"}
    cmd = ["bash", str(hook)] if hook.suffix == ".sh" else [sys.executable, str(hook)]
    proc = subprocess.run(cmd, input=json.dumps(_EVENT), env=env, capture_output=True, text=True, timeout=60)
    return proc, {p for p in state.rglob("*") if p.is_file()}


@pytest.mark.parametrize("hook", _PY_HOOKS + _SH_HOOKS, ids=lambda p: p.name)
def test_every_hook_exits_at_once_when_nested(hook: Path, tmp_path: Path) -> None:
    proc, written = _run(hook, tmp_path)
    assert proc.returncode == 0, (hook.name, proc.stderr[-400:])
    assert proc.stdout.strip() == "", (hook.name, proc.stdout[:300])
    assert not written, (hook.name, sorted(written))


def test_every_hook_the_registry_names_is_covered() -> None:
    sys.path.insert(0, str(_PLUGIN / "hooks"))
    import registry

    covered = {p.relative_to(_PLUGIN).as_posix() for p in _PY_HOOKS + _SH_HOOKS}
    assert {spec.script for spec in registry.HOOKS if spec.script} <= covered


def test_the_marker_reaches_every_claude_child() -> None:
    """Static: the drain hook exports it before the writer; the extract
    writer, HyDE, the analyzer and synthesis set it on their subprocess."""
    drain = (_PLUGIN / "hooks" / "reflect-drain-bg.sh").read_text()
    assert "export REFLECT_NESTED=1" in drain and drain.index("export REFLECT_NESTED=1") < drain.index('"${WRITER_ARGV[@]}"')
    for rel in ("scripts/drain_extract.py", "skills/recall/scripts/recall.py", "scripts/reflect_synthesis.py"):
        assert '"REFLECT_NESTED": "1"' in (_PLUGIN / rel).read_text(), rel
    analyze = (_PLUGIN.parent / "src" / "reflect_kb" / "issues" / "analyze.py").read_text()
    assert '"REFLECT_NESTED": "1"' in analyze
