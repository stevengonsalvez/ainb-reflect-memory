"""The drain writer must never be handed an unbounded transcript (issue #34).

Sessions crossed ~1MB of transcript in July 2026. Whenever no cascade slice
bounded the input — gate disabled, signal detector unavailable, cascade crash —
the drain handed the writer the whole file, and `claude -p` rejected it with
`Prompt is too long: ... > 200000 maximum` before doing any work. Ten days of
captured lessons queued up behind that.

The stub `claude` here behaves like the real one: it measures the file it is
pointed at and refuses anything over the context limit. So these are behaviour
tests of the drain's input contract, not of its wiring — if the drain stops
bounding, the stub rejects and the assertions fail.
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

# Real limit the writer model rejects at; ~4 chars/token is the usual estimate.
_CONTEXT_TOKENS = 200_000

_STUB = '''#!/usr/bin/env python3
"""Stand-in for `claude -p`: rejects an input that would not fit its context."""
import json, os, re, sys

seen_log = os.environ["STUB_SEEN_LOG"]
args = " ".join(sys.argv[1:])
size = 0
target = ""
for path in re.findall(r"(/[^\\s]+)", args):
    if os.path.isfile(path) and os.path.getsize(path) > size:
        size, target = os.path.getsize(path), path
with open(seen_log, "a") as fh:
    fh.write(json.dumps({"target": target, "chars": size}) + "\\n")

tokens = size // 4
if tokens > %d:
    print(json.dumps({
        "type": "result", "subtype": "error", "is_error": True,
        "result": "Prompt is too long: %%d tokens > %d maximum" %% tokens,
        "num_turns": 1, "total_cost_usd": 0, "usage": {},
    }))
else:
    print(json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "Captured 1 learning.", "num_turns": 2, "total_cost_usd": 0.02,
        "usage": {"input_tokens": tokens, "output_tokens": 300},
    }))
''' % (_CONTEXT_TOKENS, _CONTEXT_TOKENS)


def _stub_claude(tmp_path: Path) -> tuple[Path, Path]:
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(_STUB)
    stub.chmod(0o755)
    return stub, tmp_path / "stub-seen.jsonl"


def _huge_transcript(path: Path, target_bytes: int = 1_200_000) -> Path:
    """A real-shaped transcript well past the writer's context window."""
    rows = []
    i = 0
    size = 0
    while size < target_bytes:
        rows.append(json.dumps({
            "type": "user", "message": {"role": "user", "content":
                f"turn {i}: no, that's wrong, the root cause was a missing index "
                "on user_id and a bare except swallowed the KeyError. " + "x" * 400},
            "uuid": f"u{i}", "timestamp": "2026-08-01T10:00:00Z", "sessionId": "big"}))
        rows.append(json.dumps({
            "type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
                "content": [{"type": "text", "text":
                    f"turn {i}: fixed by catching KeyError explicitly. " + "y" * 400}],
                "usage": {"input_tokens": 10, "output_tokens": 10}},
            "uuid": f"a{i}", "timestamp": "2026-08-01T10:00:30Z", "sessionId": "big"}))
        size = sum(len(r) for r in rows)
        i += 1
    path.write_text("\n".join(rows) + "\n")
    return path


def _small_transcript(path: Path) -> Path:
    rows = [
        {"type": "user", "message": {"role": "user", "content":
            "No, that's wrong. Never use a bare except here; the root cause was "
            "a missing index on user_id."},
         "uuid": "u1", "timestamp": "2026-08-01T10:00:00Z", "sessionId": "small"},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "Fixed by catching KeyError explicitly."}],
            "usage": {"input_tokens": 50, "output_tokens": 10}},
         "uuid": "a1", "timestamp": "2026-08-01T10:00:30Z", "sessionId": "small"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _enqueue(queue: Path, transcripts: list[Path]) -> None:
    queue.write_text("".join(
        json.dumps({"session_id": t.stem, "transcript_path": str(t),
                    "trigger": "stop", "cwd": "/", "scope": "session",
                    "harness": "claude", "ts": "2026-08-01T10:01:00Z"}) + "\n"
        for t in transcripts
    ))


def _run(state: Path, stub: Path, seen_log: Path, **env_overrides):
    env = dict(os.environ)
    env.update({
        "REFLECT_STATE_DIR": str(state),
        "REFLECT_DRAIN_DRY_RUN": "0",
        "REFLECT_DRAIN_CLAUDE_BIN": str(stub),
        "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
        # Cascade OFF on purpose: this is exactly the fail-open path that used
        # to hand the writer a whole transcript.
        "REFLECT_DRAIN_CASCADE": "0",
        "REFLECT_QUOTA_GATE": "0",
        "REFLECT_DRAIN_SKIP_REINDEX": "1",     # never touch the developer's real KB
        "REFLECT_QUIET_INSTALL_WARNING": "1",
        # Pin the legacy writer: these assert the INPUT contract, which is
        # writer-independent (bounding happens before the writer is chosen).
        # Which writer runs by default is test_drain_writer_default.py's job.
        "REFLECT_DRAIN_WRITER": "agentic",
        "STUB_SEEN_LOG": str(seen_log),
    })
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(["bash", str(_DRAIN)], env=env, capture_output=True,
                          text=True, timeout=180)


def _outcomes(state: Path) -> list[str]:
    f = state / "drain-cost.jsonl"
    if not f.exists():
        return []
    return [json.loads(line)["outcome"] for line in f.read_text().splitlines() if line.strip()]


def _seen(seen_log: Path) -> list[dict]:
    if not seen_log.exists():
        return []
    return [json.loads(line) for line in seen_log.read_text().splitlines() if line.strip()]


def test_oversized_transcript_still_produces_a_learning(tmp_path):
    """A 1MB transcript must reach the writer in a form it accepts."""
    state = tmp_path / "state"
    state.mkdir()
    big = _huge_transcript(tmp_path / "big-session.jsonl")
    assert big.stat().st_size > 1_000_000
    _enqueue(state / "pending_reflections.jsonl", [big])
    stub, seen_log = _stub_claude(tmp_path)

    _run(state, stub, seen_log, REFLECT_DRAIN_MAX="1")

    log = (state / "drain.log").read_text()
    assert "ok" in _outcomes(state), (
        f"the writer rejected the transcript: outcomes={_outcomes(state)}\n{log}"
    )
    assert (state / "pending_reflections.jsonl").read_text().strip() == ""


def test_writer_never_receives_more_than_the_cap(tmp_path):
    """Whatever the drain hands over must fit inside the configured cap."""
    state = tmp_path / "state"
    state.mkdir()
    big = _huge_transcript(tmp_path / "big-session.jsonl")
    _enqueue(state / "pending_reflections.jsonl", [big])
    stub, seen_log = _stub_claude(tmp_path)

    _run(state, stub, seen_log, REFLECT_DRAIN_MAX="1",
         REFLECT_DRAIN_MAX_INPUT_CHARS="60000")

    seen = _seen(seen_log)
    assert seen, "the writer was never invoked"
    biggest = max(s["chars"] for s in seen)
    assert biggest <= 70_000, (
        f"writer was handed {biggest} chars; the cap is 60000 (+header slack)"
    )
    assert "bounded input" in (state / "drain.log").read_text()


def test_bounded_view_keeps_the_end_of_the_session(tmp_path):
    """Head+tail, not head-only: the corrections worth learning from land late."""
    state = tmp_path / "state"
    state.mkdir()
    big = _huge_transcript(tmp_path / "big-session.jsonl")
    big.write_text(big.read_text() + json.dumps({
        "type": "user", "message": {"role": "user", "content":
            "FINAL RULE: never bulk-commit, always one concern per commit."},
        "uuid": "ulast", "timestamp": "2026-08-01T11:00:00Z", "sessionId": "big"}) + "\n")
    _enqueue(state / "pending_reflections.jsonl", [big])
    stub, seen_log = _stub_claude(tmp_path)

    _run(state, stub, seen_log, REFLECT_DRAIN_MAX="1")

    seen = _seen(seen_log)
    assert seen, "the writer was never invoked"
    handed = Path(seen[-1]["target"])
    # The drain deletes the bounded file after the run, so assert on what the
    # cascade produces for the same input instead of racing the cleanup.
    from subprocess import run as _r
    cascade = Path(__file__).resolve().parents[1] / "scripts" / "reflect_cascade.py"
    out = _r(["python3", str(cascade), "bound", str(big), "--max-chars", "60000"],
             capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    body = Path(json.loads(out.stdout)["path"]).read_text()
    assert "FINAL RULE: never bulk-commit" in body, "tail of the session was dropped"
    assert "bounded input" in body, "the elision is not disclosed to the writer"
    assert handed.name.startswith("reflect-bounded-") or handed == big


def test_small_transcript_is_handed_over_untouched(tmp_path):
    """No needless indirection for input that already fits."""
    state = tmp_path / "state"
    state.mkdir()
    small = _small_transcript(tmp_path / "small-session.jsonl")
    _enqueue(state / "pending_reflections.jsonl", [small])
    stub, seen_log = _stub_claude(tmp_path)

    _run(state, stub, seen_log, REFLECT_DRAIN_MAX="1")

    seen = _seen(seen_log)
    assert seen and seen[-1]["target"] == str(small), \
        f"a small transcript was rewritten before the writer saw it: {seen}"
    assert "bounded input" not in (state / "drain.log").read_text()
    assert "ok" in _outcomes(state)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
