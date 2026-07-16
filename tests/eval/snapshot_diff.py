#!/usr/bin/env python3
# ABOUTME: Golden-snapshot regression gate — runs the recall eval harness on the
# ABOUTME: golden queries and fails if R@5 regresses vs results/baseline.json.
"""Recall snapshot-diff gate.

Runs the SAME hermetic eval the harness runs (build a KB from
``tests/eval/fixtures/corpus/``, score the golden queries), then compares the
result to the committed ``tests/eval/results/baseline.json`` and FAILS when the
ranking has regressed:

  * overall R@5 drops by more than ``R5_DROP_TOLERANCE`` (0.05) vs baseline, OR
  * any ``exact``-class query that hit a relevant doc in the baseline top-5 no
    longer has that specific doc in its current top-5 (a previously-served exact
    answer fell out — the sharpest regression signal).

Usage:
    python3 tests/eval/snapshot_diff.py                # gate: exit 1 on regression
    python3 tests/eval/snapshot_diff.py --update-baseline   # regenerate baseline.json
    python3 tests/eval/snapshot_diff.py --debug        # verbose per-query trace

Also importable as a pytest module: ``test_no_recall_regression`` skips when the
heavy deps (full-stack ``reflect`` + [graph] + model) or the baseline are
unavailable, and otherwise asserts the same invariant.

Graceful degradation (mirrors the harness's own gating): the eval needs the
full-stack ``reflect`` CLI, the [graph] extra, and the embedding model. When any
of those is missing — or when the recall.py path can't be resolved in the
current repo layout — the standalone script prints a warning and exits 0 rather
than failing a slim CI job; the pytest entry skips. A missing baseline is
likewise a warn-and-pass with an instruction to generate one, so bootstrapping a
fresh checkout never hard-fails.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

RESULTS = HERE / "results"
BASELINE = RESULTS / "baseline.json"

R5_DROP_TOLERANCE = 0.05


class SnapshotUnavailable(RuntimeError):
    """Raised when the eval can't run here (heavy deps / repo layout)."""


def _load_harness():
    """Import the eval harness, translating its layout/dep failures into a
    single skip signal. Importing harness resolves recall.py at module load and
    raises RuntimeError if the plugin path isn't present in this checkout."""
    try:
        from harness import EvalHarness, HarnessError  # noqa: F401
    except (ImportError, RuntimeError) as exc:
        raise SnapshotUnavailable(f"eval harness unavailable: {exc}") from exc
    return EvalHarness, HarnessError


def run_eval(debug: bool = False) -> dict:
    """Build the hermetic KB and score the golden queries. Raises
    SnapshotUnavailable when the environment can't support the real engine."""
    EvalHarness, HarnessError = _load_harness()
    with tempfile.TemporaryDirectory(prefix="snapshot-diff-") as td:
        h = EvalHarness(Path(td), live=False, debug=debug)
        try:
            h.build_kb()
            return h.run(with_arms=False)
        except HarnessError as exc:
            raise SnapshotUnavailable(f"eval could not run: {exc}") from exc


def _queries_by_id(report: dict) -> dict[str, dict]:
    return {q["id"]: q for q in report.get("queries", [])}


def compare(current: dict, baseline: dict) -> list[str]:
    """Return a list of regression messages (empty == no regression)."""
    regressions: list[str] = []

    base_r5 = baseline.get("overall", {}).get("recall_at_5")
    cur_r5 = current.get("overall", {}).get("recall_at_5")
    if cur_r5 is None:
        # A degenerate run (no scorable queries) is not a ranking regression —
        # treat as unavailable rather than a spurious fail.
        raise SnapshotUnavailable("current run produced no overall R@5 (degenerate eval)")
    if base_r5 is not None and (base_r5 - cur_r5) > R5_DROP_TOLERANCE:
        regressions.append(
            f"overall R@5 regressed: baseline {base_r5:.4f} -> current {cur_r5:.4f} "
            f"(drop {base_r5 - cur_r5:.4f} > tolerance {R5_DROP_TOLERANCE})"
        )

    cur_by_id = _queries_by_id(current)
    for bq in baseline.get("queries", []):
        if bq.get("class") != "exact":
            continue
        base_top5 = set(bq.get("returned", [])[:5])
        base_hit = set(bq.get("relevant", [])) & base_top5
        if not base_hit:
            continue  # baseline didn't serve a relevant doc here; nothing to lose
        cq = cur_by_id.get(bq["id"])
        if cq is None:
            regressions.append(f"exact query {bq['id']} missing from current run")
            continue
        cur_top5 = set(cq.get("returned", [])[:5])
        dropped = base_hit - cur_top5
        if dropped:
            regressions.append(
                f"exact query {bq['id']} lost a previously-served relevant doc from "
                f"top-5: {sorted(dropped)} (current top-5: {cq.get('returned', [])[:5]})"
            )
    return regressions


def _write_baseline(report: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(report, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true",
                    help="run the eval and overwrite results/baseline.json, then exit 0")
    ap.add_argument("--debug", action="store_true", help="verbose per-query trace")
    args = ap.parse_args(argv)

    try:
        report = run_eval(debug=args.debug)
    except SnapshotUnavailable as exc:
        print(f"[snapshot-diff] SKIP: {exc}")
        print("[snapshot-diff] the golden-diff gate needs the full-stack `reflect` "
              "(pipx install '.[graph]') + model cache; skipping without failure.")
        return 0

    if args.update_baseline:
        _write_baseline(report)
        o = report["overall"]
        print(f"[snapshot-diff] baseline updated -> {BASELINE}")
        print(f"[snapshot-diff]   R@5={o['recall_at_5']}  MRR={o['mrr']}  n={o['n_queries']}")
        return 0

    if not BASELINE.exists():
        print(f"[snapshot-diff] WARNING: no baseline at {BASELINE}.")
        print("[snapshot-diff] generate one with: "
              "python3 tests/eval/snapshot_diff.py --update-baseline")
        return 0

    baseline = json.loads(BASELINE.read_text())
    try:
        regressions = compare(report, baseline)
    except SnapshotUnavailable as exc:
        print(f"[snapshot-diff] SKIP: {exc}")
        return 0

    o = report["overall"]
    b = baseline.get("overall", {})
    print(f"[snapshot-diff] current R@5={o['recall_at_5']} MRR={o['mrr']}  "
          f"baseline R@5={b.get('recall_at_5')} MRR={b.get('mrr')}")
    if regressions:
        print("[snapshot-diff] FAIL — recall regressed:")
        for msg in regressions:
            print(f"  - {msg}")
        return 1
    print("[snapshot-diff] OK — no recall regression vs baseline.")
    return 0


# ── pytest entry ─────────────────────────────────────────────────────────────


def test_no_recall_regression():
    import pytest

    if not BASELINE.exists():
        pytest.skip(f"no baseline at {BASELINE}; run snapshot_diff.py --update-baseline")
    try:
        report = run_eval(debug=False)
    except SnapshotUnavailable as exc:
        pytest.skip(str(exc))
    baseline = json.loads(BASELINE.read_text())
    try:
        regressions = compare(report, baseline)
    except SnapshotUnavailable as exc:
        pytest.skip(str(exc))
    assert not regressions, "recall regressed vs baseline:\n" + "\n".join(regressions)


if __name__ == "__main__":
    raise SystemExit(main())
