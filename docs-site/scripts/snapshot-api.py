# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml>=6.0", "click>=8.0", "rich>=13.0", "httpx>=0.27"]
# ///
"""Dump every GET payload of `reflect serve` for the bundled sample KB.

Imports the REAL serve logic (src/reflect_kb/serve.py) and runs it against
docs-site/data/sample-kb/ (a COPY, so nothing is written into the tree). The
result is the static data set behind the in-browser demo:

    docs-site/src/data/api-snapshot.json   committed, deterministic
    docs-site/demo/search-expected.json    python search results for the offline
                                           parity test (scripts/test-mock-search.mjs)

Regenerate (needs python + uv; the docs build itself does NOT need python):

    uv run docs-site/scripts/snapshot-api.py

Determinism rules:
  * the clock is frozen (serve.py's browse score is age-dependent) at
    newest-note-date + 14 days, recorded as meta.now so the JS mock reuses it;
  * stats are normalised: repo path replaced, metrics come from the sample KB's
    invented metrics.jsonl (synthetic counters if it has none);
  * the output is scanned for absolute paths / home dirs and the run fails if
    any are found.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(ROOT / "src"))

import reflect_kb.serve as serve  # noqa: E402  (real server logic)
from reflect_kb.cli.learnings_cli import parse_frontmatter  # noqa: E402

DEFAULT_KB = ROOT / "docs-site" / "data" / "sample-kb"
DEFAULT_OUT = ROOT / "docs-site" / "src" / "data" / "api-snapshot.json"
DEFAULT_EXPECTED = ROOT / "docs-site" / "demo" / "search-expected.json"

# Fallback engine counters, used only when the sample KB ships no metrics.jsonl.
# (The sample KB's metrics.jsonl is itself invented; stats always come from the
# KB directory passed in, never from a local ~/.learnings.)
SYNTHETIC_OPS = {"search": 214, "rerank": 188, "embed": 96, "fleet_shadow_recall": 31}
SYNTHETIC_ERRORS = 2
SYNTHETIC_REPO = "sample-kb (demo data)"

FIXED_QUERIES = [
    "postgres",
    "POSTGRES connection pool",
    "postgres postgres",
    "docker",
    "react hooks",
    "retry backoff",
    "ci",
    "cache invalidation",
    "x-reflect",
    "a/b",
    "nonexistent-zzzz",
    "a",
    "",
    "   ",
]


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def jround(obj):
    """Round-trip through JSON exactly like serve.py's _json (default=str)."""
    return json.loads(json.dumps(obj, default=str))


def freeze_clock(now: datetime) -> None:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401
            return now if tz else now.replace(tzinfo=None)

    serve.datetime = Frozen


def doc_dates(kb_dir: Path):
    for sub in ("documents", "archived"):
        for p in sorted((kb_dir / sub).glob("*.md")):
            fm, _ = parse_frontmatter(p.read_text())
            for key in ("created", "captured_at", "updated"):
                if fm.get(key):
                    yield str(fm[key])[:19]
                    break


def derive_now(kb_dir: Path) -> datetime:
    dates = []
    for s in doc_dates(kb_dir):
        try:
            dates.append(datetime.fromisoformat(s))
        except ValueError:
            pass
    newest = max(dates) if dates else datetime(2026, 1, 1)
    return (newest + timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )


def build_queries(memories) -> list[str]:
    tags = Counter(t for m in memories for t in m["tags"])
    top_tags = [t for t, _ in tags.most_common(8)]
    ents = Counter(e for m in memories for e in m["entity_names"])
    top_ents = [e for e, _ in ents.most_common(4)]
    words = []
    for m in memories[:4]:
        words.append(" ".join(m["title"].split()[:3]))
    out, seen = [], set()
    for q in FIXED_QUERIES + top_tags + top_ents + words:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", type=Path, default=DEFAULT_KB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    ap.add_argument("--now", help="override frozen clock (ISO date)")
    args = ap.parse_args()

    kb_dir = args.kb.resolve()
    if not (kb_dir / "documents").is_dir():
        print(f"error: {kb_dir}/documents not found", file=sys.stderr)
        return 1

    now = (
        datetime.fromisoformat(args.now).replace(tzinfo=timezone.utc)
        if args.now
        else derive_now(kb_dir)
    )
    freeze_clock(now)

    with tempfile.TemporaryDirectory(prefix="reflect-snap-") as tmp:
        tmp = Path(tmp)
        live_dir = tmp / "live"
        full_dir = tmp / "full"
        for dst in (live_dir, full_dir):
            shutil.copytree(
                kb_dir, dst, ignore=shutil.ignore_patterns("README.md", ".DS_Store")
            )

        # ---- the KB exactly as shipped (what the demo opens with) ----
        kb = serve.KnowledgeBase(live_dir)
        memories = jround(kb.memories())
        details = {m["id"]: jround(kb.memory(m["id"])) for m in memories}
        graph = jround(kb.graph())
        stats = jround(kb.stats())
        archived = jround(kb.archived())
        queue = jround(kb.compress_queue())

        stats["repo"] = SYNTHETIC_REPO
        if not (kb_dir / "metrics.jsonl").exists():
            # No bundled (invented) metrics.jsonl: fall back to synthetic counters
            # so a local ~/.learnings can never leak through stats.
            stats["metrics_ops"] = dict(SYNTHETIC_OPS)
            stats["metrics_errors"] = SYNTHETIC_ERRORS

        # ---- restore every archived note on a scratch copy, so the demo can
        # offer "Restore" with full detail (the live API never exposes the body
        # of an archived note). Uses the real KnowledgeBase.restore().
        full = serve.KnowledgeBase(full_dir)
        restorable = {}
        for a in archived:
            full.restore(a["id"])
        for a in archived:
            d = jround(full.memory(a["id"]))
            d.pop("related", None)
            d.pop("browse_score", None)
            restorable[a["id"]] = d
        full_graph = jround(full.graph())
        relations = [
            {"s": e["s"], "t": e["t"], "w": e["w"]}
            for e in full_graph["edges"]
            if e["kind"] == "relation"
        ]

        # ---- search fixtures: python results under the frozen clock ----
        queries = build_queries(memories)
        expected = {
            "now": now.isoformat(),
            "queries": {
                q: [
                    {
                        "id": r["id"],
                        "match_score": r["match_score"],
                        "browse_score": r["browse_score"],
                    }
                    for r in jround(kb.search(q))
                ]
                for q in queries
            },
            # same queries once every archived note is live (exercises restore)
            "queries_full": {
                q: [
                    {
                        "id": r["id"],
                        "match_score": r["match_score"],
                        "browse_score": r["browse_score"],
                    }
                    for r in jround(full.search(q))
                ]
                for q in queries
            },
        }

    snapshot = {
        "meta": {
            "source": "docs-site/data/sample-kb",
            "generated_by": "docs-site/scripts/snapshot-api.py",
            "now": now.isoformat(),
            "note": "Invented sample data. Payloads are real reflect serve output.",
        },
        "endpoints": {
            "memories": memories,
            "memory": details,
            "graph": graph,
            "stats": stats,
            "archived": archived,
            "compress_queue": queue,
        },
        "restorable": restorable,
        "relations": relations,
    }

    text = json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n"
    exp_text = json.dumps(expected, indent=1, ensure_ascii=False) + "\n"

    # ---- leak guard ----
    bad = [str(ROOT), str(kb_dir), str(Path.home()), "/home/", "/Users/", "/tmp/", "reflect-snap-"]
    for label, blob in (("snapshot", text), ("expected", exp_text)):
        for needle in bad:
            if needle and needle in blob:
                print(f"error: {label} leaks local path fragment {needle!r}", file=sys.stderr)
                return 2
    if chr(0x2014) in (text + exp_text):
        print("error: output contains an em-dash", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.expected.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    args.expected.write_text(exp_text)
    print(
        f"snapshot: {len(memories)} live, {len(archived)} archived, "
        f"{len(graph['nodes'])} graph nodes, {len(queue['groups'])} queue groups, "
        f"{len(queries)} search queries, now={now.date()}"
    )
    print(f"  wrote {_rel(args.out)} ({len(text)//1024} KiB)")
    print(f"  wrote {_rel(args.expected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
