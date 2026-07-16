# ABOUTME: Behavioral proof for F3 — quarantine gate + fleet-context budget. A
# ABOUTME: quarantined fleet doc is absent from default recall, present under
# ABOUTME: --include-quarantined, and the fleet-context block honors its byte caps.
"""F3 quarantine-enforcement + fleet-context budget proof.

Three runtime-observable invariants over ONE seeded KB into which the real fleet
fixtures have been ingested (quarantined, and REINDEXED so they are genuinely in
the engine index — not merely on disk):

  A. QUARANTINE EXCLUDES BY DEFAULT. A default recall for a query the fleet
     corpus strongly answers (concurrent writes behind an flock — the fixture's
     ``disc-2`` discovery) returns the seeded non-quarantined note and NOT a
     single quarantined fleet id. The quarantine filter runs post-retrieval, so
     this holds even though the fleet doc is indexed and lexically on-topic.

  B. --include-quarantined OPTS BACK IN (the control for A). The SAME query with
     ``--include-quarantined`` surfaces at least one quarantined fleet id — proof
     that A's exclusion is the quarantine gate doing its job, not the fleet doc
     being unindexed or irrelevant.

  C. FLEET-CONTEXT HONORS ITS BUDGET. ``--format fleet-context`` (the fleet
     shadow's view, which implies include-quarantined) emits the
     ``fleet-context/v1`` marker, at most ``FLEET_CONTEXT_MAX_ITEMS`` (5) item
     lines, and an estimated-token size within ``FLEET_CONTEXT_MAX_TOKENS``
     (2000) — measured with recall.py's own ≈4-chars/token estimator, a pure
     byte measurement. The caps are anchored to the module source so the proof
     tracks the constants if they move.

Falsifiability: if the quarantine gate were dropped, arm A would surface a
quarantined id and FAIL. If ``--include-quarantined`` were ignored, arm B would
find no quarantined id and FAIL. If the fleet-context renderer stopped enforcing
its caps, arm C's item-count or token-budget assertion would FAIL. Arms A and B
are a matched pair over the identical query, so neither can be satisfied by a
degenerate empty result.

No LLM participates: the seed, the fixture content, the quarantine frontmatter,
the deterministic engine, and the byte estimator fully determine every
assertion.

PORT: F3
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _resolve_recall_py() -> Path:
    """Resolve recall.py the same way tests/eval/behavioral/conftest.py does —
    inline, so this proof needs no `import conftest` (which collides with
    tests/conftest.py). Covers the standalone ``plugin/`` layout and the
    monorepo ``plugins/reflect/`` layout."""
    eval_root = Path(__file__).resolve().parents[2]  # tests/eval
    candidates = [
        eval_root.parents[1] / "plugin" / "skills" / "recall" / "scripts" / "recall.py",
        eval_root.parents[2] / "plugins" / "reflect" / "skills" / "recall" / "scripts" / "recall.py",
        eval_root.parents[1].parent / "plugins" / "reflect" / "skills" / "recall" / "scripts" / "recall.py",
    ]
    return next((p for p in candidates if p.exists()), candidates[0])


RECALL_PY = _resolve_recall_py()

FLEET_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "fleet"

# Anchored to recall.py's module constants (asserted against source below, M1
# style — no hand-magic that can silently drift from the port).
_MAX_ITEMS = 5
_MAX_TOKENS = 2000
_MARKER = "fleet-context/v1"

# A seeded, visible note the default recall can legitimately return, on the same
# topic the fixture's quarantined disc-2 covers (so arm A's exclusion is a real
# choice between competitors, not an empty result).
SEED = dict(
    name="f3-seed-flock-serialize",
    title="Serialize concurrent writes behind a lock to avoid duplicate writes",
    category="concurrency",
    tags=["concurrency", "flock"],
    confidence="high",
    created="2026-05-01",
    key_insight="One flock around the read-modify-write serializes concurrent ingest.",
    body="Concurrent ingest workers produced duplicate writes until a single flock "
         "around the read-modify-write serialized them.",
)

QUERY = "serialize concurrent writes behind an flock to stop duplicate writes under concurrent ingest"


def _est_tokens(text: str) -> int:
    """recall.py's exact estimator (≈4 chars/token, floor 1) — measuring the
    fleet-context block with it measures the budget the renderer enforces."""
    return max(1, len(text) // 4)


def _quarantined_titles(documents_dir: Path) -> set[str]:
    """Titles of the quarantined (fleet-imported) docs on disk.

    The importer stamps ``quarantine: true`` but writes NO ``name:``/``id:``
    frontmatter, so recall attributes these docs the id ``"?"`` — matching them
    by id is impossible. Their ``title`` frontmatter IS populated and surfaces in
    the recall JSON, so title is the reliable identity for the quarantine check.
    """
    out: set[str] = set()
    for md in documents_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        q = fm.get("quarantine")
        is_q = q is True or (isinstance(q, str) and q.strip().lower() in ("true", "yes", "1"))
        if is_q and fm.get("title"):
            out.add(str(fm["title"]).strip().strip('"'))
    return out


def _result_titles(payload: dict) -> set[str]:
    return {
        str(r.get("title", "")).strip().strip('"')
        for r in payload.get("results", [])
        if r.get("title")
    }


def _fleet_context(env: dict) -> str:
    cmd = [
        "python3", str(RECALL_PY), QUERY,
        "--format", "fleet-context", "--no-cache",
        "--min-overlap", "0.0", "--max-tokens", str(_MAX_TOKENS),
        "--limit", str(_MAX_ITEMS), "--domain-hint", "coding",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, f"fleet-context recall exited {r.returncode}\nSTDERR:\n{r.stderr[-1200:]}"
    return r.stdout


def _reflect_on_path() -> bool:
    path = os.environ.get("RECALL_EVAL_BIN_DIR", "") + ":" + os.environ.get("PATH", "")
    return shutil.which("reflect", path=path) is not None


@pytest.mark.skipif(
    not _reflect_on_path(),
    reason="full-stack `reflect` not resolvable; set RECALL_EVAL_BIN_DIR",
)
def test_F3_quarantine_gate_and_fleet_context_budget(behavioral_kb):
    kb = behavioral_kb
    if not shutil.which("reflect", path=kb.env()["PATH"]):
        pytest.skip("`reflect` CLI not resolvable in the proof env")

    # Anchor the caps to the module source (they must not silently drift).
    src = RECALL_PY.read_text()
    assert f"FLEET_CONTEXT_MAX_ITEMS = {_MAX_ITEMS}" in src, "FLEET_CONTEXT_MAX_ITEMS drifted from the proof"
    assert f"FLEET_CONTEXT_MAX_TOKENS = {_MAX_TOKENS}" in src, "FLEET_CONTEXT_MAX_TOKENS drifted from the proof"
    assert f'FLEET_CONTEXT_MARKER = "{_MARKER}"' in src, "FLEET_CONTEXT_MARKER drifted from the proof"

    kb.seed([SEED])
    r = subprocess.run(
        ["reflect", "fleet", "ingest", "--root", str(FLEET_FIXTURES)],
        capture_output=True, text=True, env=kb.env(), timeout=1800,
    )
    assert r.returncode == 0, f"fleet ingest failed:\nSTDOUT:\n{r.stdout[-800:]}\nSTDERR:\n{r.stderr[-1200:]}"

    quarantined = _quarantined_titles(kb.kb_dir / "documents")
    assert quarantined, "fleet ingest wrote no quarantined docs — the proof would be vacuous"

    # --- Arm A: default recall excludes every quarantined fleet doc. ----------
    # Match by title (fleet docs have no name/id -> recall id is "?"); ids also
    # checked so a quarantined doc leaking as "?" can't hide.
    default = kb.recall(QUERY, limit=10)
    default_ids = [r.get("id") for r in default.get("results", [])]
    default_titles = _result_titles(default)
    assert SEED["name"] in default_ids, (
        f"the seeded visible note must be retrievable for the query; got {default_ids}"
    )
    leaked = default_titles & quarantined
    assert not leaked, (
        f"quarantined fleet docs leaked into the default (claude/codex) recall: {sorted(leaked)}"
    )

    # --- Arm B (control): --include-quarantined surfaces a fleet doc. ---------
    included = kb.recall(QUERY, limit=10, extra_args=["--include-quarantined"])
    surfaced = _result_titles(included) & quarantined
    assert surfaced, (
        "--include-quarantined must admit at least one quarantined fleet doc for a "
        f"query the fleet corpus answers; got titles {sorted(_result_titles(included))} "
        f"(quarantined: {sorted(quarantined)[:5]}…). If this is empty while arm A passed, "
        "the exclusion in A cannot be attributed to the quarantine gate."
    )

    # --- Arm C: fleet-context honors the marker + item + token budget. --------
    block = _fleet_context(kb.env())
    assert _MARKER in block, f"fleet-context block missing the {_MARKER!r} marker:\n{block[:400]}"
    item_lines = [ln for ln in block.splitlines() if ln.startswith("- **")]
    assert len(item_lines) <= _MAX_ITEMS, (
        f"fleet-context emitted {len(item_lines)} item lines, over the {_MAX_ITEMS} cap:\n{block}"
    )
    tokens = _est_tokens(block)
    assert tokens <= _MAX_TOKENS, (
        f"fleet-context block is {tokens} est tokens, over the {_MAX_TOKENS} budget:\n{block[:400]}"
    )
