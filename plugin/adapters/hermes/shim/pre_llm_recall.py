#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pre-LLM recall shim for the Hermes (fleet-lambda) harness.

A fleet-lambda hook pipes a JSON envelope on stdin::

    {"prompt": "...", "agent_id": "...", "domain_hint": "...", "session_id": "..."}

and this shim decides what — if anything — to inject ahead of the LLM turn,
based on ``FLEET_MEMORY_BACKEND``:

  * ``bank``   → exit 0 immediately, no output (fleet's own bank owns recall).
  * ``shadow`` → run recall, log telemetry, print NOTHING (default). This is
    the measurement mode for the pre-flip rollout.
  * ``reflect`` → run recall and print the fleet-context block to stdout.

Recall is a subprocess call to the deployed ``recall.py`` with the
fleet-context renderer under the BANK-parity budget. It is wall-clock bounded
by ``REFLECT_FLEET_TIMEOUT`` (default 10s). ANY exception anywhere collapses to
a silent exit 0 with an error breadcrumb at ``~/.reflect/last-event.json`` —
the shim must never surface a traceback or block the turn.

Exit behavior: always 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

_HOOK_NAME = "pre_llm_recall"

# Best-effort import of the shared silent-fail helpers. When the shim runs from
# its deployed location (~/.hermes/skills/reflect/shim/) the plugin scripts dir
# is not guaranteed to be present, so fall back to an inline breadcrumb writer
# that matches silent_fail.write_last_event's on-disk shape.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from silent_fail import write_last_event  # noqa: E402
except ImportError:
    def write_last_event(*, hook_name: str, event: str, kind: str, detail: str) -> None:
        try:
            state = Path(os.environ.get("REFLECT_STATE_DIR", str(Path.home() / ".reflect")))
            path = state / "last-event.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "event": event,
                "hook": hook_name,
                "kind": kind,
                "detail": str(detail)[:500],
                "ts": time.time(),
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass


def _metrics_path() -> Path:
    """Where fleet shadow telemetry lands.

    Honors ``REFLECT_METRICS_PATH`` (tests point it at a tmp file); defaults to
    the same ``~/.learnings/metrics.jsonl`` that :mod:`reflect_kb.metrics` and
    ``reflect metrics stats`` read.
    """
    env = os.environ.get("REFLECT_METRICS_PATH")
    if env:
        return Path(env)
    return Path.home() / ".learnings" / "metrics.jsonl"


def write_metric(op: str, **fields) -> None:
    """Append a metric event to the JSONL log. Best-effort, never raises.

    Written directly (rather than importing ``reflect_kb.metrics``) because the
    reflect_kb package is not deployed alongside the shim; the record shape
    matches ``metrics.write_metric`` so the aggregator reads it uniformly.
    """
    try:
        from datetime import datetime, timezone

        path = _metrics_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,
            "harness": "hermes",
            **fields,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def _resolve_recall_script() -> Path | None:
    """Locate the deployed recall.py, tolerating both deploy and repo layouts."""
    env = os.environ.get("REFLECT_RECALL_SCRIPT")
    if env:
        p = Path(env)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    candidates = [
        # Deployed: ~/.hermes/skills/reflect/shim/ → skills/recall/scripts/
        here.parents[2] / "recall" / "scripts" / "recall.py",
        # Repo: plugin/adapters/hermes/shim/ → plugin/skills/recall/scripts/
        here.parents[3] / "skills" / "recall" / "scripts" / "recall.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _recall_runner() -> list[str]:
    """Command prefix that executes recall.py.

    recall.py declares a pyyaml dependency in its PEP-723 header, so it runs
    via ``uv run --script`` in production. ``REFLECT_RECALL_RUNNER`` overrides
    the prefix (tests point it at a plain interpreter + stub script).
    """
    override = os.environ.get("REFLECT_RECALL_RUNNER")
    if override:
        return override.split()
    return ["uv", "run", "--script"]


def _count_hits(block: str) -> int:
    """Count rendered fleet-context items (one ``source:`` line each)."""
    return sum(1 for line in block.splitlines() if line.lstrip().startswith("source:"))


def _run_recall(prompt: str, domain_hint: str, timeout: float) -> str:
    """Run recall in fleet-context mode; return its stdout block (may be "").

    Raises on subprocess launch failure / timeout so the caller's silent-fail
    wrapper turns it into a breadcrumb.
    """
    script = _resolve_recall_script()
    if script is None:
        raise FileNotFoundError("recall.py not found in deploy or repo layout")

    cmd = [*_recall_runner(), str(script), prompt,
           "--format", "fleet-context",
           "--include-quarantined",
           "--max-tokens", "2000",
           "--limit", "5",
           "--no-followup"]
    if domain_hint:
        cmd.extend(["--domain-hint", domain_hint])

    child_env = {**os.environ, "REFLECT_HARNESS": "hermes"}
    proc = subprocess.run(
        cmd,
        input="",
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout,
    )
    # recall.py exits 0 with empty stdout on an absent/empty KB; a non-zero exit
    # (e.g. bad args) yields no usable block — treat both as "no hits".
    return proc.stdout if proc.returncode == 0 else ""


def _main_body() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    mode = os.environ.get("FLEET_MEMORY_BACKEND", "shadow").strip().lower() or "shadow"

    # bank mode: fleet-lambda's own bank owns recall — do nothing, fast.
    if mode == "bank":
        return

    prompt = str(data.get("prompt", "") or "").strip()
    if not prompt:
        return

    domain_hint = str(data.get("domain_hint", "") or "").strip()
    agent = str(data.get("agent_id", "") or "").strip()
    try:
        timeout = float(os.environ.get("REFLECT_FLEET_TIMEOUT", "10"))
    except (TypeError, ValueError):
        timeout = 10.0

    start = time.monotonic()
    block = _run_recall(prompt, domain_hint, timeout)
    latency_ms = (time.monotonic() - start) * 1000.0

    hits = _count_hits(block)
    tokens_est = max(0, len(block) // 4)

    write_metric(
        "fleet_shadow_recall",
        hits=hits,
        tokens_est=tokens_est,
        latency_ms=round(latency_ms, 1),
        agent=agent,
        mode=mode,
    )

    # Only reflect mode surfaces the block to the LLM turn; shadow logs silently.
    if mode == "reflect" and block.strip():
        sys.stdout.write(block if block.endswith("\n") else block + "\n")


def main() -> None:
    try:
        _main_body()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        detail = str(exc) or traceback.format_exc(limit=2)
        write_last_event(
            hook_name=_HOOK_NAME,
            event="error",
            kind=type(exc).__name__,
            detail=detail,
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
