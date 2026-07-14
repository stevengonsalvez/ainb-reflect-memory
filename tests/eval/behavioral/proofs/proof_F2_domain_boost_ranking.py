# ABOUTME: Behavioral proof for F2 — the --domain-hint affinity boost. Two learnings
# ABOUTME: identical except their `domain` frontmatter; the hint deterministically
# ABOUTME: promotes the matching-domain note above the other, and flips symmetrically.
"""F2 domain-affinity boost proof.

Invariant: given two learnings that are byte-identical in title and body (so the
engine assigns them the SAME base relevance) and differ ONLY in their ``domain``
frontmatter — one ``coding``, one ``personal`` — a recall run with
``--domain-hint personal`` ranks the personal note above the coding one, and a
run with ``--domain-hint coding`` flips the order. The hint is the only thing
that changes between the two runs, so the reversal can ONLY come from
``recall.domain_norm``'s affinity boost (``bounded_boost(1.0, α)`` for the
matching domain vs the neutral 0.5 norm for the other).

This is the soft-affinity contract, not hard isolation: a hintless run leaves
both notes at the neutral norm, so the pre-F3 ordering is preserved. The proof
asserts the symmetric flip (the decisive, sign-carrying observable) rather than
a specific hintless order, because equal base scores make the hintless tie-break
an engine detail, not the property under test.

``--no-mmr`` is passed so MMR diversity can't drop one of the two near-identical
notes before the boost is applied, and ``RECALL_DOMAIN_ALPHA`` is pinned so the
boost margin is comfortably above any tie-break noise. Both notes carry the same
query terms, so both are retrieved; the domain boost alone decides their order.

Falsifiability: if the domain boost were absent (``DOMAIN_ALPHA=0`` or the
feature reverted), the two identical-content notes would keep equal scores under
BOTH hints, so ``--domain-hint personal`` and ``--domain-hint coding`` would
return the SAME order — the reversal assertion would FAIL. If the boost matched
on the wrong field, the hinted note would not move and the assertion would
likewise FAIL.

No LLM participates: identical bodies, the ``domain`` frontmatter, the pinned
alpha, and the deterministic engine fully determine both orderings.

PORT: F2
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


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

# The two notes share EVERY query term; only `domain` differs. Equal base score
# => the domain boost is the sole tie-breaker.
_SHARED_TITLE = "Batch small writes and flush on a timer to cut syscall overhead"
_SHARED_BODY = (
    "Flushing every individual write hammered the syscall path. Buffering small "
    "writes and flushing them together on a short timer cut the syscall overhead "
    "dramatically while keeping the flush latency bounded."
)
_SHARED_INSIGHT = "Buffer small writes and flush on a timer to amortize syscalls."

QUERY = "batch small writes and flush on a timer to reduce syscall overhead"

NOTE_CODING = dict(name="f2-batch-writes-coding", domain="coding")
NOTE_PERSONAL = dict(name="f2-batch-writes-personal", domain="personal")


def _render(name: str, domain: str) -> str:
    """A visible (non-quarantined) learning carrying a `domain` frontmatter key."""
    lines = [
        "---",
        f"name: {name}",
        f'title: "{_SHARED_TITLE}"',
        "category: performance",
        "tags:",
        "  - performance",
        "  - io",
        "confidence: high",
        'created: "2026-05-10"',
        f'key_insight: "{_SHARED_INSIGHT}"',
        f"domain: {domain}",
        "---",
        "",
        f"## Learning\n\n{_SHARED_BODY}\n\n**How to apply:** {_SHARED_INSIGHT}\n",
    ]
    return "\n".join(lines)


def _base_env(kb_dir: Path, state_dir: Path, cache_home: Path) -> dict:
    env = dict(os.environ)
    env["GLOBAL_LEARNINGS_PATH"] = str(kb_dir)
    env["REFLECT_STATE_DIR"] = str(state_dir)
    env["XDG_CACHE_HOME"] = str(cache_home)
    env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    env.setdefault(
        "SENTENCE_TRANSFORMERS_HOME",
        str(Path.home() / ".cache" / "torch" / "sentence_transformers"),
    )
    # Pin the affinity boost strong enough to dominate tie-break noise between two
    # equal-base-score notes (default is 0.2; 0.5 keeps the margin comfortable).
    env["RECALL_DOMAIN_ALPHA"] = "0.5"
    bin_dir = os.environ.get("RECALL_EVAL_BIN_DIR")
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def _rank(env: dict, domain_hint: str | None) -> list[str]:
    cmd = [
        "python3", str(RECALL_PY), QUERY,
        "--limit", "5", "--format", "json", "--no-cache",
        "--min-overlap", "0.0", "--no-mmr",
    ]
    if domain_hint is not None:
        cmd += ["--domain-hint", domain_hint]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, f"recall.py exited {r.returncode}\nSTDERR:\n{r.stderr[-1200:]}"
    payload = json.loads(r.stdout or "{}")
    return [res.get("id") or "" for res in payload.get("results", []) if res.get("id")]


def _reflect_on_path() -> bool:
    path = os.environ.get("RECALL_EVAL_BIN_DIR", "") + ":" + os.environ.get("PATH", "")
    return shutil.which("reflect", path=path) is not None


@pytest.mark.skipif(
    not _reflect_on_path(),
    reason="full-stack `reflect` not resolvable; set RECALL_EVAL_BIN_DIR",
)
def test_F2_domain_hint_flips_ranking_of_identical_notes(tmp_path):
    kb_dir = tmp_path / "kb"
    state_dir = tmp_path / "state"
    cache_home = tmp_path / "xdg-cache"
    for d in (kb_dir, state_dir, cache_home):
        d.mkdir(parents=True, exist_ok=True)

    env = _base_env(kb_dir, state_dir, cache_home)
    if not shutil.which("reflect", path=env["PATH"]):
        pytest.skip("`reflect` CLI not resolvable in the proof env")

    r = subprocess.run(["reflect", "init"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"reflect init failed: {r.stderr[-600:]}"
    docs = kb_dir / "documents"
    docs.mkdir(exist_ok=True)
    (docs / f"{NOTE_CODING['name']}.md").write_text(_render(NOTE_CODING["name"], "coding"))
    (docs / f"{NOTE_PERSONAL['name']}.md").write_text(_render(NOTE_PERSONAL["name"], "personal"))
    r = subprocess.run(
        ["reflect", "reindex", "--force"],
        capture_output=True, text=True, env=env, timeout=1800,
    )
    assert r.returncode == 0, f"reflect reindex failed: {r.stderr[-800:]}"

    coding_id = NOTE_CODING["name"]
    personal_id = NOTE_PERSONAL["name"]

    personal_first = _rank(env, "personal")
    coding_first = _rank(env, "coding")

    # Both notes must be retrieved under each hint, or the ordering claim is moot.
    for ids, hint in ((personal_first, "personal"), (coding_first, "coding")):
        assert coding_id in ids and personal_id in ids, (
            f"both domain notes must be retrieved under --domain-hint {hint}; got {ids}"
        )

    # DECISIVE: --domain-hint personal ranks the personal note above the coding
    # one; --domain-hint coding reverses it. Identical base scores mean only the
    # domain boost can produce this sign-carrying flip.
    assert personal_first.index(personal_id) < personal_first.index(coding_id), (
        f"--domain-hint personal must rank the personal-domain note first; got {personal_first}"
    )
    assert coding_first.index(coding_id) < coding_first.index(personal_id), (
        f"--domain-hint coding must rank the coding-domain note first; got {coding_first}"
    )
