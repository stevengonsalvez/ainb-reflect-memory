"""The drain's default writer is the single-shot extract path (5.2.5).

Why the default moved: the agentic loop re-sends its whole growing conversation
every turn, so on a real 1.5MB session it walked into the 200k context wall
mid-run and the writer rejected the next call with "prompt is too long".
Bounding the input (#34) does not save it — the growth is the loop, not the
input. Measured on the two transcripts from #34: agentic = 17 turns, 1.5M
tokens, $1.07, partial_max_turns with nothing captured; extract = 1 turn, 78k
tokens, $0.40, 3 learnings written.

These drive the real drain script; `agentic` must stay reachable via the env
var, because it is still the fallback whenever no slice exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_DRAIN = Path(__file__).resolve().parents[1] / "hooks" / "reflect-drain-bg.sh"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def _stub_claude(tmp_path: Path) -> Path:
    """Stub `claude` answering BOTH writer shapes.

    extract asks for a JSON action list as the result text; the agentic loop
    just wants a plain summary. Emitting an empty action list satisfies extract
    (captures nothing, no error) and is inert prose for agentic, so a single
    stub can prove which path the drain chose.
    """
    envelope = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "[]", "num_turns": 1, "total_cost_usd": 0.01,
        "usage": {"input_tokens": 900, "output_tokens": 10},
    }
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("#!/usr/bin/env bash\n" f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n")
    stub.chmod(0o755)
    return stub


def _big_transcript(path: Path) -> Path:
    """Large enough that the #34 bound produces a slice even with the gate off."""
    rows = []
    while sum(len(r) for r in rows) < 200_000:
        i = len(rows)
        rows.append(json.dumps({
            "type": "user", "message": {"role": "user", "content":
                f"turn {i}: no, that's wrong, the root cause was a missing index. " + "x" * 300},
            "uuid": f"u{i}", "timestamp": "2026-08-11T10:00:00Z", "sessionId": "w"}))
        rows.append(json.dumps({
            "type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": f"turn {i}: fixed by catching it. " + "y" * 300}],
                "usage": {"input_tokens": 10, "output_tokens": 10}},
            "uuid": f"a{i}", "timestamp": "2026-08-11T10:00:30Z", "sessionId": "w"}))
    path.write_text("\n".join(rows) + "\n")
    return path


def _run(state: Path, stub: Path, transcript: Path, **env_overrides):
    state.mkdir(parents=True, exist_ok=True)
    (state / "pending_reflections.jsonl").write_text(json.dumps({
        "session_id": "w", "transcript_path": str(transcript), "trigger": "stop",
        "cwd": "/", "scope": "session", "harness": "claude", "ts": "2026-08-11T10:01:00Z",
    }) + "\n")
    env = dict(os.environ)
    env.update({
        "REFLECT_STATE_DIR": str(state),
        "REFLECT_DRAIN_DRY_RUN": "0",
        "REFLECT_DRAIN_CLAUDE_BIN": str(stub),
        "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
        "REFLECT_DRAIN_CASCADE": "0",
        "REFLECT_QUOTA_GATE": "0",
        "REFLECT_DRAIN_SKIP_REINDEX": "1",
        "REFLECT_QUIET_INSTALL_WARNING": "1",
        "REFLECT_DRAIN_MAX": "1",
    })
    env.pop("REFLECT_DRAIN_WRITER", None)
    env.update({k: str(v) for k, v in env_overrides.items()})
    subprocess.run(["bash", str(_DRAIN)], env=env, capture_output=True,
                   text=True, timeout=180)
    return (state / "drain.log").read_text()


def test_extract_is_the_default_writer(tmp_path):
    log = _run(tmp_path / "state", _stub_claude(tmp_path),
               _big_transcript(tmp_path / "w.jsonl"))
    assert "extract:" in log, (
        "the drain did not take the single-shot extract path by default:\n" + log
    )


def test_agentic_is_still_reachable(tmp_path):
    log = _run(tmp_path / "state", _stub_claude(tmp_path),
               _big_transcript(tmp_path / "w.jsonl"),
               REFLECT_DRAIN_WRITER="agentic")
    assert "extract:" not in log, (
        "REFLECT_DRAIN_WRITER=agentic no longer selects the legacy loop:\n" + log
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
