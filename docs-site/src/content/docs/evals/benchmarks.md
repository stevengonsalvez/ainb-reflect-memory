---
title: Benchmarks (LOCOMO)
description: Pilot results for the reflect memory engine on LOCOMO long-term conversational memory, with method, per-category scores, what helped and hurt, how to reproduce, and caveats.
sidebar:
  order: 1
---

reflect's retrieval engine was evaluated on [LOCOMO](https://github.com/snap-research/locomo) (long-term conversational memory). This is a **pilot**: 50 stratified questions (10 per category) from one conversation (`conv-26`). Numbers below are copied from the benchmark's [REPORT.md](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/tests/eval/locomo/REPORT.md) and were measured on the **reflect 4.1.0 engine**. Later releases (5.x) have not been re-benchmarked.

:::caution
n = 50 from a single conversation. Per-cell noise is about +/-0.1. Treat results as directional, not as a ranking against other systems.
:::

## Headline

With the retrieval fixes below, reflect reaches **J = 0.80** under the Opus judge, up from 0.73, balanced across every question type. J-score is LLM-judge correctness.

| Config (Opus judge) | single | multi | temporal | open | adversarial | **overall** |
|---|---|---|---|---|---|---|
| v2: mpnet + exhaustive extraction | 0.70 | 0.75 | 0.80 | 0.50 | 0.90 | **0.73** |
| + bge embedder/reranker (B) | 0.80 | 0.70 | 0.80 | 0.60 | 0.80 | **0.74** |
| **+ HyDE query expansion (D)** | 0.80 | 0.80 | 0.80 | 0.70 | 0.90 | **0.80** (best) |
| + abstention gate (F, min-overlap 0.15), dropped | 0.30 | 0.00 | 0.70 | 0.20 | 1.00 | **0.44** |

## Method

```text
sessions ──▶ extract atomic notes (claude -p) ──▶ reflect reindex (real engine)
question ──▶ recall.py (real engine, recall arms via RECALL_* env) ──▶ context
         ──▶ answer (claude -p) ──▶ judge vs gold (claude -p) ──▶ J-score
```

| Aspect | Setting |
|---|---|
| Dataset | LOCOMO `conv-26`, 50 QA, 10 per category (single-hop, multi-hop, temporal, open-domain, adversarial) |
| Answerer | Sonnet |
| Writer (dialogue to notes) | Sonnet |
| Judge | **Opus** (the calibrated reference judge, see below) |
| Retrieval | reflect's real write, reindex and recall pipeline |
| Extraction | A LOCOMO-domain adapter (exhaustive conversational extraction). The shipped writer targets coding transcripts, so porting this to the real writer is a follow-up. |
| LLM calls | `claude -p --setting-sources '' --strict-mcp-config` (clean Sonnet, OAuth, no session hooks, CLAUDE.md or MCP; no API key needed) |

The harness defines four configs: `arms_on` (every 4.1.0 recall arm knob exported `=1`), `arms_off` (every knob exported `=0`, about 4.0 behavior), `no_memory` (floor, question only) and `full_context` (ceiling, whole conversation in the prompt).

## Config tuning: 0.52 to 0.64 (Sonnet judge)

Two harness-side levers, no engine change:

| Stage | Overall (Sonnet judge) | What moved |
|---|---|---|
| baseline (top-8 / 3k chars) | 0.52 | none |
| + recall budget (top-25 / 10k) | 0.60 | multi-hop 0.10 to 0.50 |
| + exhaustive extraction (239 to 635 notes) | 0.64 | single-hop and temporal +0.20 |

## The judge matters: Opus is the reference

The same 100 answers re-graded by three judges:

| Judge | Overall J | Agreement vs Opus | Bias |
|---|---|---|---|
| haiku | 0.53 | 0.80 | rejects 20 correct answers Opus accepts (0 the other way) |
| sonnet | 0.65 | 0.92 | rejects 8 (0 reverse) |
| **opus (reference)** | **0.73** | n/a | n/a |

Cheaper judges are systematically harsh (one-directional under-crediting of valid paraphrases and dates), not noisy. Judge choice alone swings the headline by 0.20, more than any single engine fix. All headline numbers use the Opus judge.

## Engine fixes: what helped, what hurt

Each shipped as an additive, env-gated change. Defaults are unchanged and no new API key is needed.

| Fix | Engine change | Effect | Verdict |
|---|---|---|---|
| **B** embedder + reranker | `REFLECT_EMBED_MODEL` (all-mpnet to bge-base-en-v1.5, dimension auto-derived); `REFLECT_CE_MODEL` (ms-marco-MiniLM to bge-reranker-base) | single-hop 0.70 to 0.80, open 0.50 to 0.60 | keep (+0.01 net, better factual recall) |
| **D** HyDE query expansion | `REFLECT_RECALL_HYDE=1`: generate a hypothetical answer via reflect's own `claude -p`, embed it alongside the query | multi-hop 0.70 to 0.80, open 0.60 to 0.70, adversarial to 0.90 | keep, the big win (+0.06) |
| **A** recall budget | `REFLECT_RECALL_LIMIT` / `REFLECT_RECALL_MAX_CHARS` env-overridable | already applied via CLI in all runs | keep (exposes the proven lever) |
| **C** arm threshold recalibration | `recall.py --calibrate-thresholds` on the bge corpus feeds `RECALL_ARM_*_MIN_SCORE` | neutral | keep (harmless, future-useful) |
| **F** abstention / OOD gate | `REFLECT_RECALL_MIN_OVERLAP` (R7) | over-suppressed: 27/50 answers became "NOT MENTIONED" (vs 12/50), answerable QA fell to 0.44 | drop at 0.15; needs a far gentler value |
| **G** conversational extraction | realized as the benchmark's exhaustive-extraction adapter | already inside the 0.52 to 0.64 gain | porting to the real writer is a follow-up |

**Winning config:** bge-base embedder + bge-reranker + HyDE + recall arms at their default (on), recall 25 results / 10k chars, exhaustive extraction, no OOD gate. Opus-judged **0.80**.

### The 4.1.0 recall arms: not yet measured

The stored `arms_on` vs `arms_off` pairs (for example 0.80 vs 0.76 on the bge and HyDE run) are not evidence about the arms. `recall.py` treats an unset arm knob as on, and the harness's `arms_off` used to only delete the variables, so both configs ran identical retrieval and the differences are run-to-run noise. REPORT.md no longer claims the arms are positive. The harness now exports `=0` for every arm in `arms_off` (check with `python3 locomo_bench.py --print-config`), and the ablation has to be re-run: the command is on the [LOCOMO in full](/ainb-reflect-memory/evals/locomo/#were-the-410-arms-positive) page under Reproduce. What the data supports is that the embedder swap and HyDE moved the score, with the arms on in every run.

### Placement against published systems

reflect's tuned four-category mean (single, multi, temporal, open = 0.80 / 0.80 / 0.80 / 0.70) is **0.775** (the report said about 0.76 until corrected), which on the Hindsight LOCOMO leaderboard sits near Memobase (75.8) and Zep (75.1), above Mem0 (66.9). Judges differ (Opus here, GPT-4o-mini there; 15 to 20 points of swing), so this is directional placement, not a ranking. The repo's charts ([positioning](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/tests/eval/locomo/results/locomo_positioning.png), [comparison](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/tests/eval/locomo/results/locomo_comparison.png)) plot this as 77.5, the same figure.

## Reproduce

All commands run from the repo root. Requires the `claude` CLI (all LLM calls go through `claude -p`), `uv`, and the `[graph]` extra (sentence-transformers, nano-graphrag; several GB with models).

```bash
# 1. dedicated venv (the harness looks for .venv-locomo at the repo root)
uv venv .venv-locomo --python 3.12
VIRTUAL_ENV=$PWD/.venv-locomo uv pip install -e ".[graph]"

# 2. dataset (not vendored)
mkdir -p tests/eval/locomo/data
curl -sL https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o tests/eval/locomo/data/locomo10.json

# 3. run
cd tests/eval/locomo
# stratified pilot: 10 QA per category, 1 conversation
python3 locomo_bench.py --samples 0 --per-cat 10 --tag pilot
# tuned retrieval budget, Opus judge
python3 locomo_bench.py --samples 0 --per-cat 10 \
  --recall-limit 25 --recall-max-chars 10000 --judge-model opus --tag tuned
# render the markdown scorecard
python3 make_report.py results/report_<tag>.json REPORT.md
```

To reproduce the winning retrieval config, export the engine knobs before the run (the report also swapped the cross-encoder to `bge-reranker-base` via `REFLECT_CE_MODEL`, but does not record the exact model id, so look it up on Hugging Face):

```bash
export REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5
export REFLECT_RECALL_HYDE=1
```

Key harness flags (`locomo_bench.py`):

| Flag | Meaning | Default |
|---|---|---|
| `--samples` | conversation indices (0 to 9) or `all` | `0` |
| `--configs` | subset of `arms_on,arms_off,no_memory,full_context` | all four |
| `--per-cat` / `--limit-qa` | scope: first N QA of each category / first N per sample | unset |
| `--recall-limit` / `--recall-max-chars` | retrieval budget, the biggest lever on multi-hop | 8 / 3000 |
| `--recall-concurrency` | parallel `recall.py` processes (torch, RAM-bound; keep at 3 or below) | 3 |
| `--concurrency` | parallel `claude` answer and judge calls | 8 |
| `--answer-model` / `--judge-model` | models for answering and judging (`opus` is the reference judge) | `sonnet` / `sonnet` |
| `--tag` | run label, scopes the cache and output file names | `pilot` |

Runs are resumable: per-session extraction and per-QA verdicts are cached under `tests/eval/locomo/results/cache/` (tag-scoped). A full LOCOMO run (`--samples all`, 1986 QA across configs) is estimated in the report at about $1k.

## Caveats

- **Cost and latency.** HyDE adds one `claude -p` call per recall (about $0.02 per QA, plus latency). The best-config 50-QA run cost about $15.
- **Wall time.** Recall reloads sentence-transformers and nano-graphrag per `recall.py` subprocess (about 20 to 45 s), which dominates run time. Keep `--recall-concurrency` at 3 or below.
- **Statistical power.** n = 50 on a single conversation gives about +/-0.1 per cell. The B and D gains and the F regression each exceed that. The full 10-conversation set would tighten cells and allow a same-judge cross-system comparison.
- **Extraction is an adapter.** Retrieval is the real engine; dialogue-to-note extraction is a LOCOMO-specific adapter, not the shipped coding-transcript writer.
- **Defaults unchanged.** Every engine change is env-gated. The shipped plugin's default behavior is unchanged, so these numbers describe the tuned config above, not a stock install.
- **Hook default differs.** The SessionStart recall hook applies its own `REFLECT_RECALL_MIN_OVERLAP` default of 0.2 (see `plugin/skills/recall/hooks/session_start_recall.py`). The benchmark ran `recall.py` directly (no hook), where the gate defaults to off.
- **Not re-run on 5.x.** Results are for the 4.1.0 engine.

## See also

- [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/) and [retrieval features](/ainb-reflect-memory/concepts/retrieval-features/) for the arms referenced here
- [Configuration reference](/ainb-reflect-memory/reference/configuration/) for the env knobs
- [Regression suite](/ainb-reflect-memory/evals/regression-suite/) for the deterministic tests that guard recall behavior
