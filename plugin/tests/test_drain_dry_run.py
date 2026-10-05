"""REFLECT_DRAIN_DRY_RUN=1 must be side-effect free on durable state.

A dry run used to remove the entry from the queue and write a `dry_run` row to
the cost ledger that counted toward the daily cap, so trialling the drain
silently lost queued work. The cascade `prepare` pass also recorded chunk
hashes, so the real drain that followed skipped the very chunks the dry run
had only previewed.

These drive the real drain script against a scratch state dir and a throwaway
HOME and compare the whole state tree before and after.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

_DRAIN = Path(__file__).resolve().parents[1] / "hooks" / "reflect-drain-bg.sh"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")

# drain.log is the one file a dry run is allowed (and expected) to write.
_ALLOWED = {"drain.log"}


def _stub_claude(tmp_path: Path) -> Path:
    """Stub `claude` that records each call and returns a clean envelope."""
    calls = tmp_path / "claude-calls.log"
    envelope = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "captured", "num_turns": 1, "total_cost_usd": 0.01,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo called >> "{calls}"\n'
        f"cat <<'EOF'\n{json.dumps(envelope)}\nEOF\n"
    )
    stub.chmod(0o755)
    return stub


def _signal_transcript(path: Path) -> Path:
    """A transcript the cascade gate accepts (explicit corrections)."""
    rows = []
    for i in range(6):
        rows.append(json.dumps({
            "type": "user", "message": {"role": "user", "content":
                f"turn {i}: no, that's wrong, the root cause was a missing index. " + "x" * 80},
            "uuid": f"u{i}", "timestamp": "2026-08-11T10:00:00Z", "sessionId": "d"}))
        rows.append(json.dumps({
            "type": "assistant", "message": {"role": "assistant", "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": f"turn {i}: fixed by adding the index. " + "y" * 80}],
                "usage": {"input_tokens": 10, "output_tokens": 10}},
            "uuid": f"a{i}", "timestamp": "2026-08-11T10:00:30Z", "sessionId": "d"}))
    path.write_text("\n".join(rows) + "\n")
    return path


def _seed(state: Path, transcripts: list[Path]) -> bytes:
    state.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({
        "ts": "2026-08-11T10:01:00Z", "session_id": f"s{i}",
        "transcript_path": str(t), "trigger": "stop", "cwd": "/",
    }) + "\n" for i, t in enumerate(transcripts))
    (state / "pending_reflections.jsonl").write_text(body)
    return body.encode()


def _tree(root: Path) -> dict[str, str]:
    """{relative path: sha256} for every file under root, minus allowed logs."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in _ALLOWED:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _run(state: Path, home: Path, stub: Path, **env_overrides) -> str:
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "REFLECT_STATE_DIR": str(state),
        "REFLECT_DRAIN_CLAUDE_BIN": str(stub),
        "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
        "REFLECT_DRAIN_SKIP_REINDEX": "1",
        "REFLECT_QUOTA_GATE": "0",
        "REFLECT_QUIET_INSTALL_WARNING": "1",
        "REFLECT_DRAIN_WRITER": "agentic",
        "REFLECT_DRAIN_MAX": "5",
    })
    env.update({k: str(v) for k, v in env_overrides.items()})
    subprocess.run(["bash", str(_DRAIN)], env=env, capture_output=True,
                   text=True, timeout=180)
    return (state / "drain.log").read_text()


def _transcripts(tmp_path: Path, n: int = 2) -> list[Path]:
    return [_signal_transcript(tmp_path / f"t{i}.jsonl") for i in range(n)]


@pytest.mark.parametrize("cascade", ["0", "1"])
def test_dry_run_leaves_all_durable_state_untouched(tmp_path, cascade):
    state, home = tmp_path / "state", tmp_path / "home"
    queue_before = _seed(state, _transcripts(tmp_path))
    # Pre-existing counters/ledgers a dry run must not move.
    (state / "drain-cost.jsonl").write_text(json.dumps({
        "ts": "t", "day": "2020-01-01", "entries": 1, "transcript": "x",
        "outcome": "ok"}) + "\n")
    before_state = _tree(state)

    log = _run(state, home, _stub_claude(tmp_path),
               REFLECT_DRAIN_DRY_RUN="1", REFLECT_DRAIN_CASCADE=cascade)

    assert "DRY_RUN=1" in log, "the dry run must still say what it would do:\n" + log
    assert (state / "pending_reflections.jsonl").read_bytes() == queue_before
    assert _tree(state) == before_state          # queue, cost ledger, retry, poison, debounce, ...
    assert not (tmp_path / "claude-calls.log").exists()   # never calls the model
    if cascade == "1":
        # The cascade's chunk-hash bookkeeping lives in the DB under HOME.
        db = home / ".reflect" / "reflect.db"
        if db.exists():
            conn = sqlite3.connect(db)
            try:
                assert conn.execute("SELECT COUNT(*) FROM chunk_hashes").fetchone()[0] == 0
            finally:
                conn.close()


def test_dry_run_does_not_count_toward_the_daily_cap(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    _seed(state, _transcripts(tmp_path, n=1))
    stub = _stub_claude(tmp_path)
    # Cap of 1: if the dry run burned the budget the real run would be refused.
    for _ in range(2):
        _run(state, home, stub, REFLECT_DRAIN_DRY_RUN="1",
             REFLECT_DRAIN_CASCADE="0", REFLECT_DRAIN_DAILY_MAX="1")
    assert not (state / "drain-cost.jsonl").exists()
    log = _run(state, home, stub, REFLECT_DRAIN_DRY_RUN="0",
               REFLECT_DRAIN_CASCADE="0", REFLECT_DRAIN_DAILY_MAX="1")
    assert "daily cap reached" not in log
    assert (tmp_path / "claude-calls.log").read_text().count("called") == 1


def test_dry_run_then_real_run_still_reflects_the_same_chunks(tmp_path):
    """The cascade must not mark chunks seen during a dry run."""
    state, home = tmp_path / "state", tmp_path / "home"
    _seed(state, _transcripts(tmp_path, n=1))
    stub = _stub_claude(tmp_path)
    _run(state, home, stub, REFLECT_DRAIN_DRY_RUN="1", REFLECT_DRAIN_CASCADE="1")
    log = _run(state, home, stub, REFLECT_DRAIN_DRY_RUN="0", REFLECT_DRAIN_CASCADE="1")
    assert "cascade skip" not in log, "the real run skipped chunks the dry run previewed:\n" + log
    assert (tmp_path / "claude-calls.log").read_text().count("called") == 1


def test_dry_run_keeps_a_cascade_skip_entry_queued(tmp_path):
    """Even an entry the real drain would drop (no signal) stays queued."""
    state, home = tmp_path / "state", tmp_path / "home"
    quiet = tmp_path / "quiet.jsonl"
    quiet.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                 "content": "hello"}, "uuid": "u", "sessionId": "q"}) + "\n")
    queue_before = _seed(state, [quiet])
    _run(state, home, _stub_claude(tmp_path),
         REFLECT_DRAIN_DRY_RUN="1", REFLECT_DRAIN_CASCADE="1")
    assert (state / "pending_reflections.jsonl").read_bytes() == queue_before
    assert not (state / "drain-cost.jsonl").exists()


def test_normal_run_still_consumes_the_queue_and_counts(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    _seed(state, _transcripts(tmp_path))
    log = _run(state, home, _stub_claude(tmp_path),
               REFLECT_DRAIN_DRY_RUN="0", REFLECT_DRAIN_CASCADE="0")
    assert (state / "pending_reflections.jsonl").read_text().strip() == "", log
    rows = [json.loads(l) for l in (state / "drain-cost.jsonl").read_text().splitlines() if l.strip()]
    assert sum(r["entries"] for r in rows) == 2
    assert (tmp_path / "claude-calls.log").read_text().count("called") == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
