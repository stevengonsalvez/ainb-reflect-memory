"""Occurrence ledger for fleet imports.

A JSON file at ``$REFLECT_STATE_DIR/fleet-ledger.json`` mapping a document's
``content_hash`` to ``{doc_id, count, first_seen, last_seen}``. Re-importing the
same content bumps ``count`` instead of writing a new file, which is what makes
``reflect fleet ingest`` idempotent and lets a repeatedly-observed learning
surface as a promotion candidate.

Two footguns this module avoids:

1. **Lost updates.** The importer reads the ledger, mutates one entry, and
   writes it back. Two concurrent ingests (or an ingest racing the bg-drain)
   would clobber each other's increments. Every read-modify-write is wrapped in
   an ``fcntl.flock`` (mirroring ``reflect_kb.errors``), so increments serialize.
2. **Torn writes.** A crash mid-write could corrupt the ledger. Saves go through
   a tmp file + ``os.replace`` (mirroring ``reflect_kb.issues.dedupe``), so the
   on-disk file is always a complete document.

At ``count >= PROMOTION_THRESHOLD`` a ``fleet_promotion_candidate`` metric is
emitted (once, on the crossing) so Fleet can decide whether to promote the
learning out of quarantine — this module never promotes anything itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from reflect_kb.metrics import write_metric

_LEDGER_VERSION = 1

# A content_hash observed this many times is surfaced as a promotion candidate.
# Matches the spec's "count >= 3" threshold.
PROMOTION_THRESHOLD = 3


def state_dir() -> Path:
    return Path(
        os.environ.get("REFLECT_STATE_DIR", str(Path.home() / ".reflect"))
    ).expanduser()


def ledger_path() -> Path:
    return state_dir() / "fleet-ledger.json"


def _lock_path() -> Path:
    return state_dir() / "fleet-ledger.lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_ledger(path: Optional[Path] = None) -> dict:
    p = path or ledger_path()
    if not p.exists():
        return {"version": _LEDGER_VERSION, "entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": _LEDGER_VERSION, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": _LEDGER_VERSION, "entries": {}}
    return data


def save_ledger(ledger: dict, path: Optional[Path] = None) -> Path:
    """Atomic write (tmp + ``os.replace``) — never leaves a torn file on disk."""
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w", dir=p.parent, delete=False, suffix=".tmp", encoding="utf-8"
    )
    try:
        json.dump(ledger, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, p)
    return p


def record_occurrence(content_hash: str, doc_id: str, path: Optional[Path] = None) -> dict:
    """Record one observation of ``content_hash`` and return its ledger entry.

    First sight creates the entry at ``count=1``; later sights increment. The
    whole read-modify-write is serialized under an exclusive flock so parallel
    ingests cannot lose an increment. Emits the promotion-candidate metric the
    first time ``count`` reaches :data:`PROMOTION_THRESHOLD`.
    """
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(_lock_path(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            ledger = load_ledger(path)
            now = _now_iso()
            entries = ledger.setdefault("entries", {})
            entry = entries.get(content_hash)
            if entry is None:
                entry = {
                    "doc_id": doc_id,
                    "count": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
                entries[content_hash] = entry
            else:
                entry["count"] = int(entry.get("count", 0)) + 1
                entry["last_seen"] = now
                # Keep doc_id current — a re-slug from a title/body edit that
                # still hashes the same would otherwise leave a stale pointer.
                entry["doc_id"] = doc_id
            ledger.setdefault("version", _LEDGER_VERSION)
            save_ledger(ledger, path)

            if entry["count"] == PROMOTION_THRESHOLD:
                write_metric(
                    "fleet_promotion_candidate",
                    hash=content_hash,
                    doc_id=doc_id,
                    count=entry["count"],
                )
            return dict(entry)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def stats(path: Optional[Path] = None) -> dict:
    """Aggregate counts for ``reflect fleet status``."""
    ledger = load_ledger(path)
    entries = ledger.get("entries", {})
    total = len(entries)
    candidates = sum(
        1 for e in entries.values() if int(e.get("count", 0)) >= PROMOTION_THRESHOLD
    )
    occurrences = sum(int(e.get("count", 0)) for e in entries.values())
    return {
        "documents": total,
        "occurrences": occurrences,
        "promotion_candidates": candidates,
    }
