#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Post-LLM capture shim for the Hermes (fleet-lambda) harness.

A fleet-lambda hook pipes a JSON envelope on stdin after a turn::

    {"last_user_msg": "...", "last_assistant_msg": "...",
     "transcript_tail": "...", "session_id": "...", "agent_id": "..."}

and this shim enqueues an entry onto ``~/.reflect/pending_reflections.jsonl``
— the same queue the ``stop_reflect.py`` hook feeds — so the background
drainer processes it later. Classification stays in ``/reflect``; the only
cheap signal computed here is a correction heuristic: if the user's last
message carries a trigger word (no / wrong / actually / stop / don't /
should be), the entry is tagged ``priority: "high"`` so the drainer can
prioritise likely corrections.

The queue is append-only JSONL (matching stop_reflect's non-atomic append —
the drainer tolerates partial lines). ANY exception collapses to a silent
exit 0 with an error breadcrumb.

Exit behavior: always 0.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

_HOOK_NAME = "post_llm_capture"

# Best-effort import of the shared silent-fail helpers; inline fallback that
# matches silent_fail.write_last_event's on-disk shape when the plugin scripts
# dir is not deployed alongside the shim.
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


# Word-boundary correction triggers. A match tags the entry high-priority so
# the drainer treats it as a likely correction — the actual classification
# still happens downstream in /reflect.
_CORRECTION_RE = re.compile(
    r"\b(no|wrong|actually|stop|don't|dont|should be)\b",
    re.IGNORECASE,
)


def state_dir() -> Path:
    return Path(os.environ.get("REFLECT_STATE_DIR", str(Path.home() / ".reflect")))


def queue_file() -> Path:
    return state_dir() / "pending_reflections.jsonl"


def transcript_dir() -> Path:
    return state_dir() / "hermes-transcripts"


def _is_correction(text: str) -> bool:
    return bool(text) and _CORRECTION_RE.search(text) is not None


def _session_key(session_id: str) -> str:
    """Filesystem-safe transcript key from a session id (empty → "")."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id) if session_id else ""


def _session_already_queued(qfile: Path, session_id: str) -> bool:
    """True if the queue already holds a pending entry for this session.

    Mirrors stop_reflect.py's dedupe: while an entry is still pending we append
    the turn to the same transcript file instead of enqueuing a duplicate. Once
    the drainer has processed and removed the entry, a later turn re-enqueues
    (the drain's chunk-hash dedup then skips content it already reflected on).
    Any read error → False (better to enqueue twice than drop silently).
    """
    if not session_id or not qfile.exists():
        return False
    try:
        with open(qfile, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("session_id") == session_id:
                    return True
    except OSError:
        return False
    return False


def _append_transcript(
    path: Path, last_user_msg: str, last_assistant_msg: str, transcript_tail: str,
) -> None:
    """Append this turn to a synthesized JSONL transcript the drain can read.

    Each line matches the shape ``reflect_gate.extract_dialogue`` parses:
    ``{"message": {"role": ..., "content": ...}}``. The drain hands the file
    path to ``/reflect``; a plain user/assistant dialogue is all it needs. May
    raise on write failure — the caller lets that surface as a silent-fail
    breadcrumb so we never enqueue a pointer to a file that was not written.
    """
    lines: list[dict] = []
    if last_user_msg:
        lines.append({"message": {"role": "user", "content": last_user_msg}})
    if last_assistant_msg:
        lines.append({"message": {"role": "assistant", "content": last_assistant_msg}})
    if not lines and transcript_tail:
        # No structured turn, but a tail blob — expose it so the gate can scan it.
        lines.append({"message": {"role": "user", "content": transcript_tail}})

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec) + "\n")


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

    last_user_msg = str(data.get("last_user_msg", "") or "")
    last_assistant_msg = str(data.get("last_assistant_msg", "") or "")
    transcript_tail = str(data.get("transcript_tail", "") or "")
    session_id = str(data.get("session_id", "") or "").strip()
    agent_id = str(data.get("agent_id", "") or "").strip()

    # Nothing worth queuing if the turn carried no content at all.
    if not (last_user_msg or last_assistant_msg or transcript_tail):
        return

    # Session-keyed transcript file, appended across turns of the same session.
    # No session id → a unique per-capture file (still gets a real path).
    key = _session_key(session_id)
    if key:
        transcript_path = transcript_dir() / f"{key}.jsonl"
    else:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        transcript_path = transcript_dir() / f"ts-{stamp}-{os.getpid()}.jsonl"

    # Write the transcript FIRST. A failure here raises → silent-fail breadcrumb
    # and NO queue entry, so the queue never points at a missing file.
    _append_transcript(transcript_path, last_user_msg, last_assistant_msg, transcript_tail)

    qf = queue_file()
    # Dedupe: only enqueue once per pending session; later turns just extend the
    # transcript. Sessionless captures always enqueue (unique transcript each).
    if key and _session_already_queued(qf, session_id):
        return

    entry = {
        # stop_reflect.py's five-field shape — transcript_path is load-bearing;
        # every drain consumer keys on it.
        "ts": datetime.now().isoformat(),
        "session_id": session_id or "unknown",
        "transcript_path": str(transcript_path),
        "trigger": "stop",
        "cwd": str(data.get("cwd", "") or os.getcwd()),
        # Additive fields (safe — consumers ignore unknown keys).
        "source": "hermes",
        "agent_id": agent_id,
    }
    if _is_correction(last_user_msg):
        entry["priority"] = "high"

    qf.parent.mkdir(parents=True, exist_ok=True)
    # Append, not atomic — the JSONL queue is append-only and the drainer
    # tolerates partial lines (mirrors stop_reflect.py).
    with open(qf, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


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
