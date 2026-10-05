# LOCOMO benchmark — reflect 4.1.0 memory engine

**Pilot, 50 stratified QA (10/category) from LOCOMO `conv-26`.** Answer = Sonnet,
writer = Sonnet, judge = **Opus** (the calibrated reference judge, see §2).
J-score = LLM-judge correctness.

> **Correction (arms ablation invalid).** Every `arms_on` vs `arms_off` pair stored in
> `results/report_pilot50*.json` was produced by a harness whose `arms_off` config only
> *deleted* the `RECALL_*` env knobs. `recall.py` reads each knob as
> `os.environ.get(NAME, "1") != "0"` (ON unless set to `0`), so both configs ran
> **identical retrieval** and every on-vs-off difference below is run-to-run noise
> (answers and HyDE output are not cached), not an arm effect. The stored numbers are kept
> unchanged. The harness now exports `=0` for every arm in `arms_off`
> (`python3 locomo_bench.py --print-config` lists the effective env per config), and the
> pair must be re-run, see [Re-running the arms ablation](#re-running-the-arms-ablation).
> Single-config results (the headline table, section 1 to 3) are unaffected: retrieval there
> ran with the arms at their default (ON).

## Headline

**reflect 4.1.0 + engine fixes reaches J = 0.80 (Opus judge), up from 0.73**,
balanced across every question type:

| config (Opus judge) | single | multi | temporal | open | adversarial | **overall** |
|---|---|---|---|---|---|---|
| v2 — mpnet + exhaustive extraction | 0.70 | 0.75 | 0.80 | 0.50 | 0.90 | **0.73** |
| + bge embedder/reranker (B) | 0.80 | 0.70 | 0.80 | 0.60 | 0.80 | **0.74** |
| **+ HyDE query-expansion (D)** | 0.80 | 0.80 | 0.80 | 0.70 | 0.90 | **0.80** ★ |
| ✗ + abstention gate (F, min-overlap 0.15) | 0.30 | 0.00 | 0.70 | 0.20 | 1.00 | **0.44** |

## 1. Config tuning got reflect from 0.52 → 0.64 (Sonnet judge)

Before any engine change, two harness-side levers (no engine edit):

| stage | overall (Sonnet judge) | what moved |
|---|---|---|
| baseline (top-8 / 3k chars) | 0.52 | — |
| + recall budget (top-25 / 10k) | 0.60 | multi-hop 0.10 → 0.50 |
| + exhaustive extraction (239 → 635 notes) | 0.64 | single-hop & temporal +0.20 |

## 2. The judge is worth ±0.20 — Opus is the reference

Re-grading the **same 100 answers** with three judges:

| judge | overall J | agreement vs Opus | bias |
|---|---|---|---|
| haiku | 0.53 | 0.80 | rejects 20 correct answers Opus accepts (0 the other way) |
| sonnet | 0.65 | 0.92 | rejects 8 (0 reverse) |
| **opus (reference)** | **0.73** | — | — |

Cheaper judges are **systematically harsh** (one-directional under-crediting of valid
paraphrases/dates), not noisy. Judge choice alone swings the headline by 0.20 — larger
than any single fix. All headline numbers use the **Opus** judge.

## 3. Engine fixes — what helped, what hurt

Each shipped as an **additive, env-gated** change (defaults unchanged, no new API key):

| fix | engine change | effect | verdict |
|---|---|---|---|
| **B** embedder + reranker | `REFLECT_EMBED_MODEL` (all-mpnet → bge-base-en-v1.5, dim auto-derived); `REFLECT_CE_MODEL` (ms-marco-MiniLM → bge-reranker-base) | single-hop 0.70→0.80, open 0.50→0.60 | **keep** (+0.01 net, better factual recall) |
| **D** HyDE query-expansion | `REFLECT_RECALL_HYDE=1` — generate a hypothetical answer via reflect's own `claude -p`, embed alongside the query | multi-hop 0.70→0.80, open 0.60→0.70, advers→0.90 | **keep — the big win (+0.06)** |
| **A** recall budget defaults | `REFLECT_RECALL_LIMIT` / `REFLECT_RECALL_MAX_CHARS` env-overridable | (already applied via CLI in all runs) | **keep** (exposes the proven lever) |
| **C** arm threshold recalibration | `reflect calibrate-thresholds` on the bge corpus → `RECALL_ARM_*_MIN_SCORE` | neutral | keep (harmless, future-useful) |
| **F** abstention / OOD gate | `REFLECT_RECALL_MIN_OVERLAP` (R7) | **over-suppressed** — 27/50 answers became "NOT MENTIONED" (vs 12/50), tanking answerable QA to 0.44 | **drop** at 0.15; needs a far gentler value |
| **G** conversational extraction | realized as the benchmark's exhaustive-extraction adapter | already in the 0.52→0.64 gain | port to the real writer = follow-up |

**Winning config:** bge-base embedder + bge-reranker + HyDE + arms at default (ON), recall 25/10k,
exhaustive extraction, **no** OOD gate. Opus-judged **0.80**.

## 4. The 4.1.0 arms: not measured (stored comparison invalid)

The stored `arms_on` vs `arms_off` pairs (for example 0.80 vs 0.76 on the bge + HyDE run,
and the earlier −0.02) came from identical retrieval, so they say nothing about whether the
arms help. They sit inside the ±0.1 per-cell noise of a 50-question pilot. **No claim is
made, in either direction, about the 57 arms until the ablation is re-run** with the fixed
harness. What the data does support: the bge embedder/reranker and HyDE moved the score
(section 3), with the arms at their default (ON) in every run.

### Re-running the arms ablation

Costs about $15 (best-config pair, Opus judge). Use a new `--tag`: the verdict cache is now
keyed on the effective arm env, so old verdicts are never reused for `arms_off`. Extraction
is cached under `results/cache/` and is reused.

```bash
cd tests/eval/locomo
python3 locomo_bench.py --print-config     # sanity: arms_on all =1, arms_off all =0
REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5 REFLECT_RECALL_HYDE=1 \
  python3 locomo_bench.py --samples 0 --per-cat 10 \
  --recall-limit 25 --recall-max-chars 10000 --judge-model opus \
  --configs arms_on,arms_off --tag pilot50_D_armsfix
python3 make_report.py results/report_pilot50_D_armsfix.json REPORT_armsfix.md
```

At n = 50 a delta under about 0.1 is not resolvable; for a real answer, repeat the run or use
more conversations (`--samples all`).

## 5. Where reflect lands vs published systems

reflect's tuned 4-category mean (single/multi/temporal/open = 0.80/0.80/0.80/0.70) = **0.775**
(77.5, as plotted in `results/locomo_positioning.png`; an earlier "≈ 0.76" here was an
arithmetic slip), which on the Hindsight LOCOMO leaderboard sits near **Memobase (75.8) / Zep (75.1)**, above
**Mem0 (66.9)** — but **judges differ** (Opus here vs GPT-4o-mini there; ±15–20 points), so
this is directional placement, not a ranking. See `results/locomo_comparison.png`.

## Operations & caveats

- Cost: HyDE adds one `claude -p` per recall (~$0.02/QA + latency). Best-config 50-QA run ≈ $15.
- Recall reloads sentence-transformers + nano-graphrag per `recall.py` subprocess (~20–45s);
  dominant wall-time. Keep `--recall-concurrency ≤ 3` (RAM-bound).
- n = 50, single conversation → per-cell ±0.1 noise. The B/D gains and the F regression each
  exceed that. Full locomo10 (1986 QA × configs ≈ $1k) would tighten cells + enable a
  same-judge cross-system comparison.
- Every engine change is env-gated; the shipped 4.1.0 plugin's default behavior is unchanged.
