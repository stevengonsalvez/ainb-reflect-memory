from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
WATCH = PLUGIN_ROOT / "hooks" / "reflect-maintenance-watch.sh"


def _run_watch(state: Path, learnings: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update({
        "REFLECT_STATE_DIR": str(state),
        "GLOBAL_LEARNINGS_PATH": str(learnings),
        "REFLECT_WATCH_FORCE_JSON_ERRORS": "1",
        "REFLECT_WATCH_SKIP_LAUNCHD": "1",
    })
    env.update(extra_env)
    return subprocess.run(
        [str(WATCH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _errors(state: Path) -> list[dict]:
    path = state / "errors.json"
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("errors", [])


def test_watch_surfaces_stale_drain_lock(tmp_path: Path) -> None:
    state = tmp_path / "state"
    learnings = tmp_path / "learnings"
    lock = state / "drain.lock.d"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999")
    (state / "pending_reflections.jsonl").write_text(
        json.dumps({"transcript_path": "/tmp/example.jsonl"}) + "\n"
    )

    cp = _run_watch(state, learnings, REFLECT_WATCH_DISABLE_INGEST="1")

    assert cp.returncode == 0, cp.stderr
    assert any(e.get("kind") == "drain_stale_lock" for e in _errors(state))


def test_watch_surfaces_missing_ingest_log(tmp_path: Path) -> None:
    state = tmp_path / "state"
    learnings = tmp_path / "learnings"
    state.mkdir()
    learnings.mkdir()

    cp = _run_watch(state, learnings)

    assert cp.returncode == 0, cp.stderr
    assert any(e.get("kind") == "ingest_never_ran" for e in _errors(state))


def test_watch_surfaces_missing_drain_launchd_when_enabled(tmp_path: Path) -> None:
    state = tmp_path / "state"
    learnings = tmp_path / "learnings"
    state.mkdir()
    learnings.mkdir()
    (state / "pending_reflections.jsonl").write_text(
        json.dumps({"transcript_path": "/tmp/example.jsonl"}) + "\n"
    )

    cp = _run_watch(
        state,
        learnings,
        REFLECT_WATCH_SKIP_LAUNCHD="0",
        REFLECT_WATCH_DISABLE_INGEST="1",
        REFLECT_WATCH_DRAIN_LABEL=f"com.reflect.test.missing.{os.getpid()}",
        REFLECT_WATCH_MAINTENANCE_LABEL=f"com.reflect.test.missing-maint.{os.getpid()}",
    )

    assert cp.returncode == 0, cp.stderr
    if os.uname().sysname == "Darwin":
        assert any(e.get("kind") == "drain_launchd_missing" for e in _errors(state))
