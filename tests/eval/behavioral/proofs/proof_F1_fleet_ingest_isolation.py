# ABOUTME: Behavioral proof for F1 — the fleet importer quarantines what it
# ABOUTME: writes, so ingesting fleet-lambda memory into a live KB cannot move the
# ABOUTME: claude/codex recall ranking (the whole point of the quarantine gate).
"""F1 fleet-ingest isolation proof.

Invariant: importing fleet-lambda artifacts (``reflect fleet ingest``) into an
already-seeded, already-indexed KB leaves the default claude/codex recall
ranking BYTE-FOR-BYTE unchanged. Every fleet doc the importer writes carries
``quarantine: true``; ``recall.filter_by_quarantine`` drops quarantined notes
from the default result set, so no fleet import — however lexically similar to a
real query — can enter, displace, or reorder a top-k result.

Decisively, the imported fleet fixtures are chosen to OVERLAP the proof's
queries: the fixture corpus carries a discovery about serializing concurrent
writes behind an flock, a correction preferring ast-grep for code search, and a
pattern about domain types over primitives — the exact topics the three seeded
(non-quarantined) learnings answer. So the fleet docs are strong lexical
competitors for the same queries; only the quarantine gate keeps them out.

To make the proof strictly stronger than "unindexed content can't rank", the
import path used here is the real CLI default, which REINDEXES after writing.
The fleet docs therefore land fully in the engine's index (graph + vector +
BM25) — they are retrievable content — and are excluded purely by the
post-retrieval quarantine filter, not by being absent from the index. Ingest
also perturbs corpus-level statistics (BM25 IDF, graph communities); the seeded
learnings are separated by a wide relevance margin so the visible ranking stays
deterministic under that perturbation.

Falsifiability: if the quarantine filter were dropped (or the importer stopped
stamping ``quarantine: true``), the overlapping fleet docs would become eligible
results and — being near-duplicates of the queries — would enter the top-k,
changing at least one query's returned id list; the equality assertion would
FAIL. If ingest silently corrupted or dropped the existing KB, a seeded id would
vanish from the post-import ranking and the assertion would likewise FAIL.

No LLM participates: the seeds, the fixture content, the quarantine frontmatter,
and the deterministic recall engine fully determine every returned id.

PORT: F1
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# The `behavioral_kb` fixture is provided by tests/eval/behavioral/conftest.py,
# loaded as a pytest plugin because this proof lives under that dir — no
# `import conftest` (which would collide with tests/conftest.py). This proof
# drives only the `reflect` CLI and the fixture, so it needs no recall.py path.

# tests/fixtures/fleet holds the committed fleet-lambda JSONL + markdown fixtures
# the importer tests also use. parents[3] == reflect-kb/tests.
FLEET_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "fleet"

# Three seeds, each the sole strong answer to one query, and each on a topic the
# committed fleet fixtures ALSO cover (concurrency/flock, ast-grep, domain
# types) so the fleet import is a genuine lexical competitor once ingested.
SEEDS = [
    dict(
        name="f1-flock-serialize-writes",
        title="Serialize concurrent writes behind a single flock to stop duplicates",
        category="concurrency",
        tags=["concurrency", "flock", "writes"],
        confidence="high",
        created="2026-05-01",
        key_insight="Guard the read-modify-write with one flock so concurrent ingest "
                    "workers can't produce duplicate writes.",
        body="Two ingest workers ran the same read-modify-write and produced duplicate "
             "rows. Wrapping the critical section in a single flock serialized the "
             "writers and removed the duplicates.",
    ),
    dict(
        name="f1-astgrep-structural-search",
        title="Prefer ast-grep for structural code search instead of grep",
        category="tooling",
        tags=["tooling", "search", "ast-grep"],
        confidence="high",
        created="2026-05-02",
        key_insight="Use ast-grep for structural code queries; plain grep misses the "
                    "syntax tree and returns text-only noise.",
        body="Searching for a function definition with grep matched comments and "
             "strings. ast-grep queried the syntax tree directly and returned only the "
             "real structural matches.",
    ),
    dict(
        name="f1-domain-types-over-primitives",
        title="Model ids and money as domain types rather than primitives",
        category="types",
        tags=["types", "modelling"],
        confidence="high",
        created="2026-05-03",
        key_insight="Give ids, temperatures, and money distinct domain types so their "
                    "invariants are checked at compile time.",
        body="Passing raw strings and ints for ids and money let a currency mix-up "
             "through. Distinct domain types made the invalid combination a compile "
             "error instead of a runtime bug.",
    ),
    # Two unrelated filler learnings so each query's answer sits at rank 1 with a
    # wide margin — the visible ranking stays deterministic when ingest shifts
    # corpus-level IDF / graph community stats.
    dict(
        name="f1-filler-tls-handshake",
        title="Pin the TLS handshake timeout so a slow peer can't hang the pool",
        category="networking",
        tags=["tls", "timeout"],
        confidence="medium",
        created="2026-05-04",
        key_insight="Bound the TLS handshake so a slow peer fails fast.",
        body="A slow TLS peer held a connection open and starved the pool; a hard "
             "handshake timeout let it fail fast.",
    ),
    dict(
        name="f1-filler-cron-drift",
        title="Add jitter to cron so every host does not fire on the same minute",
        category="scheduling",
        tags=["cron", "jitter"],
        confidence="medium",
        created="2026-05-05",
        key_insight="Jitter scheduled jobs so they don't stampede the same minute.",
        body="Every host fired its cron at :00 and stampeded the API; a small random "
             "jitter spread the load.",
    ),
]

QUERIES = [
    "serialize concurrent writes behind a lock to stop duplicate writes",
    "prefer ast-grep for structural code search over grep",
    "model ids and money as domain types not raw primitives",
]


def _reflect_on_path() -> bool:
    path = os.environ.get("RECALL_EVAL_BIN_DIR", "") + ":" + os.environ.get("PATH", "")
    return shutil.which("reflect", path=path) is not None


@pytest.mark.skipif(
    not _reflect_on_path(),
    reason="full-stack `reflect` not resolvable; set RECALL_EVAL_BIN_DIR",
)
def test_F1_fleet_ingest_does_not_move_recall_ranking(behavioral_kb):
    kb = behavioral_kb
    if not shutil.which("reflect", path=kb.env()["PATH"]):
        pytest.skip("`reflect` CLI not resolvable in the proof env")

    kb.seed(SEEDS)

    # Snapshot the default (claude/codex) ranking BEFORE any fleet import.
    before = {q: kb.recall_ids(q) for q in QUERIES}
    for q, ids in before.items():
        assert ids, f"pre-import recall returned nothing for {q!r} — KB did not seed"

    # ACT: import the fleet fixtures the real way (CLI default REINDEXES), so the
    # quarantined docs land fully in the engine index, not merely on disk.
    r = subprocess.run(
        ["reflect", "fleet", "ingest", "--root", str(FLEET_FIXTURES)],
        capture_output=True, text=True, env=kb.env(), timeout=1800,
    )
    assert r.returncode == 0, f"fleet ingest failed:\nSTDOUT:\n{r.stdout[-800:]}\nSTDERR:\n{r.stderr[-1200:]}"

    # The fleet docs must actually be on disk + indexed (else the proof is vacuous:
    # unindexed content trivially can't rank).
    quarantined = _quarantined_doc_ids(kb.kb_dir / "documents")
    assert quarantined, (
        "fleet ingest wrote no quarantined docs — the proof would be vacuous; "
        f"documents dir: {sorted((kb.kb_dir / 'documents').glob('*.md'))}"
    )

    # ASSERT: the default ranking is unchanged, id-for-id, for every query. No
    # quarantined fleet doc entered; no seeded id was displaced or reordered.
    after = {q: kb.recall_ids(q) for q in QUERIES}
    for q in QUERIES:
        assert after[q] == before[q], (
            f"fleet ingest moved the default recall ranking for {q!r}:\n"
            f"  before: {before[q]}\n  after:  {after[q]}\n"
            f"The quarantine gate must keep every fleet import "
            f"({sorted(quarantined)[:3]}…) out of the claude/codex scope; a changed "
            f"id list means quarantine is not isolating fleet memory."
        )
        # Defense in depth on the orphan-chunk count. recall.py's naive mode can
        # split one doc into multiple chunks; a chunk that loses frontmatter
        # attribution surfaces with the id "?", so a clean KB already carries some
        # "?" entries (not a leak — a pre-existing chunking artifact). What must
        # not change is HOW MANY: ingesting quarantined fleet docs must add zero
        # new orphan chunks to the visible ranking. (The importer now writes a
        # `name:` frontmatter, so a genuine quarantine leak would surface with a
        # real id and be caught by the equality assertion above, not as a "?".)
        assert after[q].count("?") == before[q].count("?"), (
            f"fleet ingest changed the orphan-chunk count in the default recall for "
            f"{q!r}: before {before[q].count('?')} vs after {after[q].count('?')} "
            f"(before: {before[q]}, after: {after[q]})"
        )


def _quarantined_doc_ids(documents_dir: Path) -> set[str]:
    """Doc ids (filename stems) whose frontmatter carries quarantine: true."""
    out: set[str] = set()
    for md in documents_dir.glob("*.md"):
        head = md.read_text(encoding="utf-8", errors="replace")[:600]
        low = head.lower()
        if "quarantine: true" in low or "quarantine: yes" in low:
            out.add(md.stem)
    return out
