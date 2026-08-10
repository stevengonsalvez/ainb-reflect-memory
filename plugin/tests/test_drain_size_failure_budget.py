"""A size failure must not eat the daily budget (issue #34, defect 2).

The 2026-07-31 outage: session transcripts crossed the writer's context limit,
so `claude -p` returned `Prompt is too long: ... > 200000 maximum` before
spending a single token. The drainer classified that as `poisoned`, archived
the entry AND charged it one unit of `REFLECT_DRAIN_DAILY_MAX`. Twenty
oversized transcripts therefore consumed the entire day's budget, and every
healthy entry behind them was starved for the rest of the day. The only
operator-visible symptom was:

    daily cap reached (today=20 >= 20); exiting

which reads like ordinary throttling. Nothing was learned for 10 days.

These tests drive the REAL drain script with a stub `claude` in an isolated
REFLECT_STATE_DIR, so they fail if the guard regresses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

_DRAIN = Path(__file__).resolve().parents[1] / "hooks" / "reflect-drain-bg.sh"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")

# What claude -p actually returns when the input does not fit its context.
_TOO_LONG = {
    "type": "result", "subtype": "error", "is_error": True,
    "result": "Prompt is too long: 250000 tokens > 200000 maximum",
    "num_turns": 1, "total_cost_usd": 0, "usage": {},
}
_HEALTHY = {
    "type": "result", "subtype": "success", "is_error": False,
    "result": "Captured 1 learning.", "num_turns": 2, "total_cost_usd": 0.01,
    "usage": {"input_tokens": 1200, "output_tokens": 400},
}
# A poison marker that is NOT a size failure — the account ran dry. That one
# still burns budget (it is not the input's fault, but retrying it is real
# spend), so the exemption must not swallow it.
_BROKE = {
    "type": "result", "subtype": "error", "is_error": True,
    "result": "Your credit balance is too low to continue.",
    "num_turns": 1, "total_cost_usd": 0, "usage": {},
}


def _stub_claude(tmp_path: Path, oversized_marker: str = "toobig") -> Path:
    """Stub `claude` that fails on the oversized transcript and succeeds else.

    The drain passes the target path inside the prompt, so the stub can decide
    per entry exactly like the real binary does per input size.
    """
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'args="$*"\n'
        f'if [[ "$args" == *{oversized_marker}* ]]; then\n'
        f"cat <<'EOF'\n{json.dumps(_TOO_LONG)}\nEOF\n"
        "else\n"
        f"cat <<'EOF'\n{json.dumps(_HEALTHY)}\nEOF\n"
        "fi\n"
    )
    stub.chmod(0o755)
    return stub


def _stub_fixed(tmp_path: Path, envelope: dict, name: str = "claude-fixed") -> Path:
    stub = tmp_path / "bin" / name
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n" f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n"
    )
    stub.chmod(0o755)
    return stub


def _transcript(path: Path) -> Path:
    """A transcript with enough signal to reach the writer."""
    rows = [
        {"type": "user", "message": {"role": "user", "content":
            "No, that's wrong. Never use a bare except here, it swallowed the "
            "KeyError and the root cause was a missing index on user_id."},
         "uuid": "u1", "timestamp": "2026-08-01T10:00:00Z", "sessionId": "s1"},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "Fixed by catching KeyError explicitly."}],
            "usage": {"input_tokens": 500, "output_tokens": 100}},
         "uuid": "a1", "timestamp": "2026-08-01T10:00:30Z", "sessionId": "s1"},
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


def _run(state: Path, stub: Path, **env_overrides) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "REFLECT_STATE_DIR": str(state),
        "REFLECT_DRAIN_DRY_RUN": "0",
        "REFLECT_DRAIN_CLAUDE_BIN": str(stub),
        "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
        "REFLECT_DRAIN_CASCADE": "0",          # exercise the writer, not the gate
        "REFLECT_QUOTA_GATE": "0",
        "REFLECT_DRAIN_SKIP_REINDEX": "1",     # never touch the developer's real KB
        "REFLECT_QUIET_INSTALL_WARNING": "1",
        "REFLECT_DRAIN_MAX_RETRIES": "10",
    })
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(["bash", str(_DRAIN)], env=env, capture_output=True,
                          text=True, timeout=120)


def _events(state: Path) -> list[dict]:
    f = state / "drain-cost.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_oversized_entry_does_not_starve_the_next_healthy_entry(tmp_path):
    """THE regression from #34: one oversized transcript must not consume the
    day's budget and lock out the healthy entry behind it."""
    state = tmp_path / "state"
    state.mkdir()
    oversized = _transcript(tmp_path / "toobig-session.jsonl")
    healthy = _transcript(tmp_path / "fine-session.jsonl")
    _enqueue(state / "pending_reflections.jsonl", [oversized, healthy])
    stub = _stub_claude(tmp_path)

    # A budget of ONE entry for the whole day. Pre-fix, the oversized entry
    # spends it and the healthy entry never runs.
    _run(state, stub, REFLECT_DRAIN_MAX="1", REFLECT_DRAIN_DAILY_MAX="1")
    _run(state, stub, REFLECT_DRAIN_MAX="1", REFLECT_DRAIN_DAILY_MAX="1")

    outcomes = [e["outcome"] for e in _events(state)]
    log = (state / "drain.log").read_text()
    assert "ok" in outcomes, (
        f"the healthy entry never reached the writer: outcomes={outcomes}\n{log}"
    )
    assert (state / "pending_reflections.jsonl").read_text().strip() == "", \
        "queue did not advance past the oversized entry"


def test_size_failure_is_quarantined_off_budget(tmp_path):
    """The oversized entry is archived, but with entries=0 and its own outcome."""
    state = tmp_path / "state"
    state.mkdir()
    oversized = _transcript(tmp_path / "toobig-session.jsonl")
    _enqueue(state / "pending_reflections.jsonl", [oversized])

    _run(state, _stub_claude(tmp_path), REFLECT_DRAIN_MAX="1")

    events = _events(state)
    assert events, "drain recorded no cost event"
    last = events[-1]
    assert last["outcome"] == "quarantine_oversized", \
        f"size failure kept the budget-burning outcome: {last['outcome']}"
    assert last["entries"] == 0, \
        f"size failure charged {last['entries']} to the daily cap; it must be free"
    # Archived, not silently dropped, and out of the queue.
    assert (state / "poison-reflections.jsonl").read_text().strip()
    assert (state / "pending_reflections.jsonl").read_text().strip() == ""
    assert "SIZE QUARANTINE" in (state / "drain.log").read_text()


def test_non_size_poison_still_costs_budget(tmp_path):
    """The exemption is for input-size wedges only, not for every poison."""
    state = tmp_path / "state"
    state.mkdir()
    transcript = _transcript(tmp_path / "broke-session.jsonl")
    _enqueue(state / "pending_reflections.jsonl", [transcript])

    _run(state, _stub_fixed(tmp_path, _BROKE), REFLECT_DRAIN_MAX="1")

    last = _events(state)[-1]
    assert last["outcome"] == "poison_writer_drift", \
        f"a non-size poison changed path: {last['outcome']}"
    assert last["entries"] == 1, "a non-size poison must still count against the cap"


def test_cap_message_separates_successes_from_failures(tmp_path):
    """'daily cap reached' must say what the budget actually bought.

    The bare message hid a total outage for 10 days: identical text whether the
    day produced 20 learnings or 20 failures.
    """
    state = tmp_path / "state"
    state.mkdir()
    day = _today()
    (state / "drain-cost.jsonl").write_text("".join(
        json.dumps({"ts": f"{day}T0{i}:00:00Z", "day": day, "entries": 1,
                    "transcript": f"/t/{i}.jsonl", "outcome": "poison_writer_drift",
                    "model": "sonnet", "tokens": 0, "writer_class": "poisoned"}) + "\n"
        for i in range(2)
    ))
    _enqueue(state / "pending_reflections.jsonl", [_transcript(tmp_path / "s.jsonl")])

    _run(state, _stub_fixed(tmp_path, _HEALTHY, "claude-ok"),
         REFLECT_DRAIN_DAILY_MAX="2")

    log = (state / "drain.log").read_text()
    assert "daily cap reached" in log
    assert "successes=0 failures=2" in log, \
        f"cap message does not distinguish spend from burn:\n{log}"
    assert "WARNING" in log, "a cap reached entirely by failures must be loud"


def test_healthy_days_still_hit_the_cap(tmp_path):
    """Successes must keep consuming the budget — the cap still has to bite."""
    state = tmp_path / "state"
    state.mkdir()
    day = _today()
    (state / "drain-cost.jsonl").write_text(
        json.dumps({"ts": f"{day}T01:00:00Z", "day": day, "entries": 1,
                    "transcript": "/t/a.jsonl", "outcome": "ok",
                    "model": "sonnet", "tokens": 100, "writer_class": "valid"}) + "\n"
    )
    _enqueue(state / "pending_reflections.jsonl", [_transcript(tmp_path / "s.jsonl")])

    _run(state, _stub_fixed(tmp_path, _HEALTHY, "claude-ok"),
         REFLECT_DRAIN_DAILY_MAX="1")

    log = (state / "drain.log").read_text()
    assert "daily cap reached" in log and "successes=1 failures=0" in log
    assert (state / "pending_reflections.jsonl").read_text().strip(), \
        "the cap must still block work once the budget bought real learnings"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
