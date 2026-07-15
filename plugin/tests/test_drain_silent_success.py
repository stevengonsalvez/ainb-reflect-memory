"""The drain must never score a no-op run as success, or lose the queue to one.

Regression cover for the 11-day outage. `claude -p "/reflect ..."` hit an
unresolved slash command, printed `Unknown command: /reflect`, and exited 0
with an envelope carrying no turns. The drain logged `outcome: ok, tokens: 0`
and, because "ok" drops the entry, discarded the transcript unharvested.

An unresolved command is an INSTALL-level fault: the plugin's skills are not
registered, so every entry fails identically. The drain must abort the run and
leave the queue intact rather than charge the fault to each transcript's retry
budget (which would poison the entire queue within 3 drains).

These tests drive the REAL drain script with a stub `claude`, so they fail if
the guard regresses.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_DRAIN = Path(__file__).resolve().parents[1] / "hooks" / "reflect-drain-bg.sh"

# The exact envelope claude -p returns for an unresolved slash command.
_OUTAGE = {
    "type": "result", "subtype": "success", "is_error": False,
    "result": "Unknown command: /reflect", "num_turns": 0,
    "total_cost_usd": 0, "usage": {},
}


def _stub_claude(tmp_path: Path, envelope: dict, exit_code: int = 0) -> Path:
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n"
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub


def _transcript(tmp_path: Path, name: str = "session.jsonl") -> Path:
    """A transcript with enough signal to clear the gate and reach the writer."""
    t = tmp_path / name
    rows = [
        {"type": "user", "message": {"role": "user", "content":
            "No, that's wrong. Never use a bare except here, it swallowed the "
            "KeyError and the root cause was a missing index on user_id. "
            "Always catch the specific exception."},
         "uuid": "u1", "timestamp": "2026-07-15T10:00:00Z", "sessionId": "s1"},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
            "content": [{"type": "text", "text":
                "Understood, the bug was a missing index on user_id and the bare "
                "except hid it. Fixed by catching KeyError explicitly."}],
            "usage": {"input_tokens": 500, "output_tokens": 100}},
         "uuid": "a1", "timestamp": "2026-07-15T10:00:30Z", "sessionId": "s1"},
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return t


class Drain:
    """One isolated drain install: its own state dir, queue, and stub claude."""

    def __init__(self, tmp_path: Path, envelope: dict, exit_code: int = 0):
        self.state = tmp_path / "state"
        self.state.mkdir(exist_ok=True)
        self.queue = self.state / "pending_reflections.jsonl"
        self.transcript = _transcript(tmp_path)
        self.stub = _stub_claude(tmp_path, envelope, exit_code)
        self.tmp_path = tmp_path

    def enqueue(self) -> None:
        self.queue.write_text(json.dumps({
            "session_id": "s1", "transcript_path": str(self.transcript),
            "trigger": "stop", "cwd": str(self.tmp_path), "scope": "session",
            "harness": "claude", "ts": "2026-07-15T10:01:00Z",
        }) + "\n")

    def run(self) -> None:
        subprocess.run(["bash", str(_DRAIN)], timeout=180, capture_output=True, env={
            **os.environ,
            "REFLECT_STATE_DIR": str(self.state),
            "REFLECT_DRAIN_DRY_RUN": "0",
            "REFLECT_DRAIN_MAX": "1",
            "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
            "REFLECT_DRAIN_CLAUDE_BIN": str(self.stub),
            "REFLECT_DRAIN_CASCADE": "0",       # exercise the writer, not the gate
            "REFLECT_QUOTA_GATE": "0",
            "REFLECT_DRAIN_SKIP_REINDEX": "1",  # never touch the developer's real KB
        })

    @property
    def outcomes(self) -> list[str]:
        f = self.state / "drain-cost.jsonl"
        if not f.exists():
            return []
        return [json.loads(l)["outcome"] for l in f.read_text().splitlines() if l.strip()]

    @property
    def queued(self) -> bool:
        return bool(self.queue.read_text().strip())

    @property
    def poisoned(self) -> bool:
        f = self.state / "poison-reflections.jsonl"
        return f.exists() and bool(f.read_text().strip())


pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def test_unknown_command_is_not_recorded_as_ok(tmp_path):
    """The exact 11-day-outage envelope must not read as success."""
    d = Drain(tmp_path, _OUTAGE)
    d.enqueue()
    d.run()
    assert d.outcomes, "drain recorded no cost event; it never reached the writer"
    assert "ok" not in d.outcomes, (
        f"drain scored a zero-turn run as ok: {d.outcomes}. "
        "This is the silent success that hid the outage for 11 days."
    )
    assert "fail_unknown_command" in d.outcomes, f"expected fail_unknown_command, got {d.outcomes}"


def test_outage_never_consumes_the_queue(tmp_path):
    """A persistent outage must not poison the queue, however long it runs.

    The retry-budget version of this guard kept the entry for exactly 3 drains
    and archived it on the 4th, so an 11-day outage still lost every transcript.
    Five consecutive drains must leave the entry exactly where it was.
    """
    d = Drain(tmp_path, _OUTAGE)
    d.enqueue()
    for i in range(5):
        d.run()
        assert d.queued, f"entry dropped from the queue on drain {i + 1}; transcript lost unharvested"
        assert not d.poisoned, f"entry poisoned on drain {i + 1}; an install fault was charged to the transcript"
    # Guard against a vacuous pass: prove the drain actually ran each time.
    assert d.outcomes.count("fail_unknown_command") == 5, (
        f"expected 5 recorded failures, got {d.outcomes}"
    )


@pytest.mark.parametrize("num_turns", [0, 0.0, None, "0"], ids=["int", "float", "null", "str"])
def test_zero_turns_detected_whatever_the_json_type(tmp_path, num_turns):
    """A null or float num_turns must not slip past the guard as a live run."""
    d = Drain(tmp_path, {**_OUTAGE, "num_turns": num_turns})
    d.enqueue()
    d.run()
    assert "ok" not in d.outcomes, f"num_turns={num_turns!r} bypassed the guard: {d.outcomes}"
    assert "fail_unknown_command" in d.outcomes, f"num_turns={num_turns!r} -> {d.outcomes}"


def test_zero_turns_with_cache_tokens_still_caught(tmp_path):
    """A no-op that reports bootstrap cache tokens is still a no-op.

    Keying the guard on tokens as well as turns would let this restore the
    data-losing 'ok' path.
    """
    d = Drain(tmp_path, {**_OUTAGE, "usage": {"cache_read_input_tokens": 4200}})
    d.enqueue()
    d.run()
    assert "ok" not in d.outcomes, f"zero-turn run with cache tokens scored ok: {d.outcomes}"
    assert "fail_unknown_command" in d.outcomes


def test_real_work_still_records_ok(tmp_path):
    """The guard must not fire on a genuine run; that would wedge the drain."""
    d = Drain(tmp_path, {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "Captured 1 learning.", "num_turns": 2, "total_cost_usd": 0.31,
        "usage": {"input_tokens": 1200, "output_tokens": 400,
                  "cache_read_input_tokens": 800, "cache_creation_input_tokens": 200},
    })
    d.enqueue()
    d.run()
    assert "ok" in d.outcomes, f"a real run with turns must record ok, got {d.outcomes}"
    assert "fail_unknown_command" not in d.outcomes, "guard fired on a genuine run"
