---
title: LOCOMO benchmark in full
description: The complete LOCOMO long-term-memory pilot for reflect 4.1.0, with dataset, harness, every stored run and variant, judge calibration, positioning against other memory systems, and verified reproduction steps.
sidebar:
  order: 2
---

This is the long form of the LOCOMO work. The [Benchmarks](/ainb-reflect-memory/evals/benchmarks/) page has the headline and a short method; this page has every stored run, how the harness works, which numbers are comparable and which are not, and how to re-run it. Retrieval stages referred to here are described in [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/), [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/) and the [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).

:::caution[Preliminary, 4.1.0 engine]
One conversation (`conv-26`), 50 questions (10 per category), per-cell noise about +/-0.1. All runs were made on the **4.1.0** recall engine in June 2026. Releases 5.0 to 5.2.5 have not been re-benchmarked. Treat results as directional.
:::

## Dataset

[LOCOMO](https://github.com/snap-research/locomo) is a long-term conversational memory benchmark: multi-session dialogues between two people, each with a QA set. The harness reads `locomo10.json` (not vendored; downloaded at run time).

| Fact (checked against the downloaded file) | Value |
|---|---|
| Conversations | 10 (`conv-26`, `conv-30`, `conv-41`, `conv-42`, `conv-43`, `conv-44`, `conv-47`, `conv-48`, `conv-49`, `conv-50`) |
| QA pairs, all conversations | 1986 |
| Category 1 (multi-hop) | 282 |
| Category 2 (temporal) | 321 |
| Category 3 (open-domain) | 96 |
| Category 4 (single-hop) | 841 |
| Category 5 (adversarial, unanswerable) | 446 |
| `conv-26` (the pilot) | 19 sessions, 199 QA (32 multi-hop, 37 temporal, 13 open-domain, 70 single-hop, 47 adversarial) |

The pilot takes the first 10 questions of each category from `conv-26`, so open-domain is covered by 10 of its 13 questions.

## Harness

Location: [`tests/eval/locomo/`](https://github.com/stevengonsalvez/ainb-reflect-memory/tree/main/tests/eval/locomo).

| File | Role |
|---|---|
| `locomo_bench.py` | Ingest, recall, answer, judge, score. Resumable. |
| `make_report.py` | Render a `results/report_<tag>.json` into a markdown scorecard (no LLM). |
| `calibrate_judge.py` | Re-grade cached answers with several judge models, report agreement vs Opus. |
| `plot_locomo.py`, `plot_positioning.py` | Produce `results/locomo_comparison.png` and `results/locomo_positioning.png`. |
| `results/` | Committed: `report_pilot50*.json`, `judge_calibration_*.json`, `arm_thresholds_bge.txt`, two PNGs. Ignored: `results/kb/`, `results/cache/`, logs. |

```text
sessions ──▶ extract atomic notes (claude -p) ──▶ write .md into hermetic KB ──▶ reflect reindex --force
question ──▶ recall.py (the shipped script, --no-cache, --confidence ANY) ──▶ context
         ──▶ answer (claude -p, "answer only from MEMORY, else NOT MENTIONED")
         ──▶ judge vs gold (claude -p) ──▶ J-score
```

- **Only retrieval is the real engine.** The dialogue to note step is a LOCOMO-specific extraction prompt inside the harness, because the shipped writer is tuned for coding transcripts. Notes are written as `documents/<session>-<n>-<slug>.md` with `category: conversation`.
- **Hermetic state.** Each conversation gets its own KB under `results/kb/` and its own `REFLECT_STATE_DIR`, so a run never touches `~/.learnings` or `~/.reflect`.
- **LLM calls** go through `claude -p --setting-sources '' --strict-mcp-config --output-format json`: clean model, OAuth login, no hooks, CLAUDE.md or MCP, no API key.
- **Adversarial scoring.** For category 5 the gold answer is "NOT MENTIONED" and the judge accepts only a refusal.
- **Recall subprocesses** run in their own process group with a 90 s kill (the engine occasionally wedges), `--recall-concurrency` default 3.

### Configs

| Config | What the answerer gets |
|---|---|
| `arms_on` | `recall.py` with every arm knob exported `=1`: `RECALL_GRAPH_ARM`, `RECALL_CROSS_ENCODER`, `RECALL_MMR`, `RECALL_TEMPORAL`, `RECALL_TEMPORAL_ARM`, `RECALL_BITEMPORAL_EDGES`, `RECALL_FUZZY_CACHE`, `RECALL_FOLLOWUP`, `REFLECT_TIERED_INJECT` |
| `arms_off` | `recall.py` with the same nine knobs exported `=0` |
| `no_memory` | The question only (floor) |
| `full_context` | The entire conversation in the prompt (ceiling) |

`recall.py` reads each arm knob as `os.environ.get(NAME, "1") != "0"`, so an unset knob is **on**. `arms_off` therefore has to export `=0`, not remove the variable. Two of the nine are inert inside this harness: `REFLECT_TIERED_INJECT` is a SessionStart hook knob (opt-in, default off, never read by `recall.py`), and `RECALL_FUZZY_CACHE` has nothing to act on because the harness passes `--no-cache`. The R8 bounded boosts, token economics and the opt-in R7/R12 gates are not arms and are identical in both configs.

`python3 locomo_bench.py --print-config` prints the effective env per config without touching data or a model, and fails if a listed knob is not read by the engine. `tests/test_locomo_arms_off.py` proves over a toy KB and a fake engine CLI (no model, no API) that `arms_off` retrieves differently from `arms_on` and that each knob changes behaviour on its own. The verdict cache is keyed on the effective arm env, so a verdict cached under the old behaviour is never reused.

:::caution[Stored `arms_on` / `arms_off` pairs were measured with identical retrieval]
Every pair in the runs table below was produced by an earlier harness whose `arms_off` only deleted the variables. Because the arms default to on, both configs ran the same retrieval, and every `arms_on` minus `arms_off` difference is run-to-run noise (extraction is cached, but answers and HyDE output are not). The stored numbers are kept as recorded, but they say nothing about whether the arms help. The harness is fixed; the pair has to be re-run (see [Reproduce](#reproduce)). Reports written by the fixed harness carry an `arm_env` field, and `make_report.py` marks any report without it as an invalid ablation.
:::

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--samples` | `0` | Comma list of conversation indexes (0 to 9) or `all` |
| `--configs` | all four | Subset of the configs above |
| `--per-cat N` | none | First N QA of each category (balanced pilot) |
| `--limit-qa N` | none | First N QA overall (smoke test) |
| `--recall-limit` | 8 | Top-K notes retrieved. The biggest lever on multi-hop. |
| `--recall-max-chars` | 3000 | Characters of memory injected |
| `--recall-concurrency` | 3 | Parallel `recall.py` processes (RAM bound) |
| `--concurrency` | 8 | Parallel `claude -p` answer/judge calls |
| `--answer-model` | `sonnet` | Answerer |
| `--judge-model` | `sonnet` | Judge. Use `opus` for the reference figures. |
| `--tag` | `pilot` | Names the report file and scopes the verdict cache |

Extraction results and per-QA verdicts are cached under `results/cache/` (verdicts scoped by `--tag`), so an interrupted run resumes.

## Runs

Each `results/report_pilot50*.json` is one stored run. Scores below are `j_score` as stored (overall) with per-category values, all on the same 50 questions. `n_notes` is the number of memory notes the extraction produced. Cost is `total_cost_usd` for answering plus judging that config.

The report files record the answerer (`"model": "sonnet"`) but **not which judge graded them**. The README and REPORT attribute the B and D rows to the Opus judge and the earlier rows to Sonnet; the table follows that attribution.

| Tag | What changed | n_notes | Recall budget | Config | single | multi | temporal | open | adv | **overall** | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pilot50` | baseline | 239 | top-8 / 3000 chars | arms_on | 0.5 | 0.1 | 0.5 | 0.5 | 1.0 | **0.52** | $6.67 |
| | | | | arms_off | 0.4 | 0.2 | 0.5 | 0.5 | 1.0 | **0.52** | $3.80 |
| | | | | no_memory | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | **0.20** | $5.27 |
| | | | | full_context | 0.9 | 0.8 | 0.6 | 0.2 | 0.9 | **0.68** | $11.48 |
| `pilot50_tuned` | bigger recall budget | 239 | top-25 / 10000 | arms_on | 0.5 | 0.5 | 0.5 | 0.4 | 1.0 | **0.58** | $5.26 |
| | | | | arms_off | 0.5 | 0.5 | 0.5 | 0.5 | 1.0 | **0.60** | $2.13 |
| `pilot50_v2` | exhaustive extraction (mpnet embedder) | 635 | top-25 / 10000 | arms_on | 0.6 | 0.5 | 0.6 | 0.5 | 0.9 | **0.62** | $5.72 |
| | | | | arms_off | 0.6 | 0.5 | 0.7 | 0.5 | 0.9 | **0.64** | $1.66 |
| `pilot50_B` | bge embedder and reranker | 635 | top-25 / 10000 | arms_on | 0.8 | 0.7 | 0.8 | 0.6 | 0.8 | **0.74** | $8.27 |
| | | | | arms_off | 0.7 | 0.8 | 0.8 | 0.6 | 0.8 | **0.74** | $3.23 |
| `pilot50_D` | B plus HyDE | 635 | top-25 / 10000 | arms_on | 0.8 | 0.8 | 0.8 | 0.7 | 0.9 | **0.80** | $7.96 |
| | | | | arms_off | 0.9 | 0.8 | 0.7 | 0.7 | 0.7 | **0.76** | $7.23 |
| `pilot50_ALL` | D plus abstention gate (`min-overlap` 0.15) | 635 | top-25 / 10000 | arms_on | 0.3 | 0.0 | 0.7 | 0.2 | 1.0 | **0.44** | $8.37 |
| | | | | arms_off | 0.2 | 0.0 | 0.7 | 0.2 | 0.9 | **0.40** | $6.31 |

The best-config 50-question run (`pilot50_D`, both configs) cost about $15, matching REPORT.md.

Read each `arms_on` / `arms_off` pair as two noisy samples of the same retrieval (see the caution above), not as an ablation.

Reading the table:

- **No memory scores 0.20, all from adversarial.** The base model cannot answer anything else, so memory is doing the work.
- **Full context scores 0.68** under the baseline-era judge, with open-domain at 0.2. The later 0.80 is under a different judge (Opus), so the two are not directly comparable.
- **Recall budget is the first lever.** Going from top-8/3000 to top-25/10000 lifted multi-hop from 0.1 to 0.5 (`arms_on` 0.1 to 0.5, `arms_off` 0.2 to 0.5; both ran the same retrieval, so the two columns are repeat samples).
- **The abstention gate was a regression**, see below.

### Opus-judged headline table

This is the table in the README and REPORT. Rows for B and D equal the stored `arms_on` rows above. The first row is different: it is the Opus re-grade of the `pilot50_v2` answers (100 answers, `arms_on` and `arms_off` pooled, from `judge_calibration_pilot50_v2.json`), which is why a per-category cell can read 0.75 on 10 questions. The committed files hold only its overall figure (0.73).

| Config (Opus judge) | single | multi | temporal | open | adv | **overall** |
|---|---|---|---|---|---|---|
| v2: mpnet + exhaustive extraction | 0.70 | 0.75 | 0.80 | 0.50 | 0.90 | **0.73** |
| + bge embedder/reranker (B) | 0.80 | 0.70 | 0.80 | 0.60 | 0.80 | **0.74** |
| + HyDE query expansion (D) | 0.80 | 0.80 | 0.80 | 0.70 | 0.90 | **0.80** (best) |
| + abstention gate (F, min-overlap 0.15), dropped | 0.30 | 0.00 | 0.70 | 0.20 | 1.00 | **0.44** |

The 0.73 to 0.80 comparison therefore mixes a pooled 100-answer figure with a single-config 50-answer figure. The B and D rows are the cleaner pair.

## Judge calibration

`calibrate_judge.py` re-grades already-cached answers with each judge, isolating the judge effect.

| Answer set | n | Haiku | Sonnet | Opus (reference) |
|---|---|---|---|---|
| `pilot50_v2` | 100 | 0.53 | 0.65 | 0.73 |
| `pilot50_D` | 100 | 0.59 | 0.70 | 0.77 |

On `pilot50_v2`, agreement with Opus was 0.80 for Haiku (it rejected 20 answers Opus accepted and none the other way) and 0.92 for Sonnet (8 and 0). Cheaper judges are one-directionally harsh toward valid paraphrases and date formats, not randomly noisy. Judge choice alone moves the headline by about 0.20, more than any single engine change, which is why every headline figure uses the Opus judge.

## Engine changes tried

Each change was an additive, env-gated edit. Defaults were left alone, and none needs a new API key. "Code today" is where the knob lives in this repo.

| Fix | Knob | Result | Verdict | Code today |
|---|---|---|---|---|
| A. recall budget | `REFLECT_RECALL_LIMIT` (default 10), `REFLECT_RECALL_MAX_CHARS` (default 2000) | multi-hop 0.1 to 0.5 | keep | `recall.py` `DEFAULT_LIMIT`, `DEFAULT_MAX_CHARS` |
| B. stronger embedder and reranker | `REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5`, `REFLECT_CE_MODEL` | single-hop 0.70 to 0.80, open 0.50 to 0.60, overall +0.01 | keep | `model_daemon.py` (`EMBEDDING_MODEL_NAME`, cross-encoder name); dimension derived from the model, a swap needs a fresh `reflect reindex` |
| C. per-arm threshold recalibration | `RECALL_ARM_{VECTOR,BM25,GRAPH,TEMPORAL}_MIN_SCORE` | neutral | keep | `recall.py --calibrate-thresholds`, `CALIBRATED_FLOORS` |
| D. HyDE query expansion | `REFLECT_RECALL_HYDE=1` | multi-hop 0.70 to 0.80, open 0.60 to 0.70, adversarial 0.80 to 0.90, overall +0.06 | keep, the big win | `recall.py` `_hyde_expand` |
| F. abstention / OOD gate | `REFLECT_RECALL_MIN_OVERLAP` (0.15) | answerable QA fell to 0.44; 27 of 50 answers became NOT MENTIONED vs 12 of 50 | dropped at 0.15 | `recall.py` `--min-overlap`; SessionStart uses 0.2 |
| G. conversational extraction | harness adapter | inside the 0.52 to 0.64 gain | follow-up: port to the real writer | `locomo_bench.py` `EXTRACT_SYS` only |

Calibrated floors saved with the run (`results/arm_thresholds_bge.txt`, 40 sampled docs, 10th percentile; vector n=800, BM25 n=0): vector 0.167, BM25 0.15, graph 0.0 (open), temporal 0.05. The BM25 sample size of 0 means the lexical arm was not exercised in the calibration environment, which fits QMD not being installed there. That is an inference, not something the report states.

HyDE reuses reflect's own headless `claude -p` (model from `REFLECT_DRAIN_MODEL`, default `sonnet`, 60 s timeout), generates one invented answer sentence, and appends it to the query. A failure falls back to the raw query. It adds one model call per recall (about $0.02 per question in REPORT.md) and is off by default.

The REPORT text says the arm-threshold step is `reflect calibrate-thresholds`. There is no such `reflect` subcommand in this repo; the entry point is `python3 plugin/skills/recall/scripts/recall.py --calibrate-thresholds`.

### Were the 4.1.0 arms positive?

Unknown. An earlier REPORT section 4 concluded the arms turned positive with bge and HyDE (`arms_on` 0.80 vs `arms_off` 0.76). That pair came from identical retrieval, so the +0.04 is noise and the conclusion has been withdrawn in REPORT.md. What the data does support: the arms were on in every stored run, and the stronger embedder plus answer-shaped queries are what moved the score. At n = 50 a delta under about 0.1 is not resolvable even with a correct ablation, so treat the re-run as a first look and repeat it (or use more conversations) before drawing a conclusion.

## Where reflect sits

![LOCOMO positioning: reflect vs published memory systems](/ainb-reflect-memory/img/evals-locomo-positioning.png)

Values as plotted by `plot_positioning.py` (LLM-judge J, percent, 4 categories):

| System | Score | Note |
|---|---|---|
| ByteRover 2.0 | 96.1 | self-reported, own harness |
| Backboard | 90.0 | self-reported |
| Honcho | 89.9 | self-reported |
| Hindsight (Gemini-3) | 89.6 | Hindsight repo |
| Hindsight (OSS-120B) | 85.7 | Hindsight repo |
| Hindsight (OSS-20B) | 83.2 | Hindsight repo |
| **reflect, Opus judge** | **77.5** | pilot, plotted as +/-6 (about 1 standard error at n=50) |
| Memobase | 75.8 | Hindsight repo |
| Zep | 75.1 | Hindsight repo (the Mem0 paper reports about 66) |
| **reflect, Sonnet judge** | **70.0** | pilot |
| Mem0-Graph | 68.4 | |
| Mem0 | 66.9 | |
| LangMem | 58.1 | |
| OpenAI | 52.9 | |

How to read it:

- **77.5 is the 4-category mean**: (0.80 + 0.80 + 0.80 + 0.70) / 4 over single, multi, temporal and open-domain, leaving out adversarial as most published numbers do. An earlier README said 76.2 and an earlier REPORT.md said "about 0.76"; both were arithmetic slips, corrected (the README in commit `1df981337`, REPORT section 5 afterwards) to 0.775.
- **The two reflect bars are not on the same basis.** The Opus bar is the 4-category mean. The Sonnet bar (70.0) matches the 5-category pooled overall in `judge_calibration_pilot50_D.json` (0.70, Sonnet), so it includes adversarial.
- **Harnesses and judges differ across the field** (the same Zep reads 75 on one harness and about 66 on another), so this is directional placement, not a ranking. reflect lands mid-field, on par with Memobase and Zep and above Mem0; the newest systems score higher but are self-reported.
- The dashed "full-context 72.9" line in the chart has no source recorded in this repo. The stored `full_context` run scored 0.68 over five categories.

For a feature comparison of these systems see [Comparison](/ainb-reflect-memory/start/comparison/).

## Reproduce

Run from the repo root. The harness looks for the venv at `.venv-locomo/` in the repo root and for `plugin/skills/recall/scripts/recall.py`; both paths were checked against `locomo_bench.py`.

```bash
# 1. dedicated venv with this checkout's reflect (the harness puts its bin/ on PATH)
uv venv .venv-locomo --python 3.12
UV_TORCH_BACKEND=cpu VIRTUAL_ENV=$PWD/.venv-locomo uv pip install -e ".[graph]"

# 2. dataset (not vendored; the directory must exist first)
mkdir -p tests/eval/locomo/data
curl -sL https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o tests/eval/locomo/data/locomo10.json

# 3. stratified pilot, 10 QA per category, conversation 0 (conv-26)
cd tests/eval/locomo
python3 locomo_bench.py --samples 0 --per-cat 10 --tag pilot

# best config: bigger budget, bge embedder, HyDE, Opus judge
REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5 REFLECT_RECALL_HYDE=1 \
  python3 locomo_bench.py --samples 0 --per-cat 10 \
  --recall-limit 25 --recall-max-chars 10000 --judge-model opus --tag tuned

# re-grade cached answers with several judges
python3 calibrate_judge.py --tag pilot50_D --judges haiku,sonnet,opus

# effective arm env per config (no data, no model); arms_on all =1, arms_off all =0
python3 locomo_bench.py --print-config

# re-run the arms ablation with the fixed harness (about $15, Opus judge).
# Use a NEW tag; extraction is cached and reused.
REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5 REFLECT_RECALL_HYDE=1 \\
  python3 locomo_bench.py --samples 0 --per-cat 10 \\
  --recall-limit 25 --recall-max-chars 10000 --judge-model opus \\
  --configs arms_on,arms_off --tag pilot50_D_armsfix
python3 make_report.py results/report_pilot50_D_armsfix.json REPORT_armsfix.md

# render a scorecard from any stored or new report (works offline)
python3 make_report.py results/report_pilot50_D.json REPORT.md
```

Full benchmark (`--samples all`, 1986 QA across the configs) is about $1k and hours, per REPORT.md; it has not been run.

What was and was not verified for this page: the dataset URL returns 200 and the structure and counts above were read from the file; `make_report.py` renders the stored `report_pilot50_D.json` offline; every flag in the table was read from `parse_args`. The model-calling steps (`locomo_bench.py` runs, `calibrate_judge.py`) were not executed here because they spend model budget; `--print-config` and `tests/test_locomo_arms_off.py` spend none and were run. The exact `REFLECT_CE_MODEL` value used for fix B (the REPORT says a bge reranker) is not recorded in the repo, so the command above leaves the cross-encoder at its default.

Caveats for any reproduction:

- **Extraction prompt.** The committed `EXTRACT_SYS` is the exhaustive one (635 notes). The 239-note baseline extraction prompt is not preserved, so the first three rows of the runs table cannot be regenerated exactly.
- **Wall time.** Each `recall.py` subprocess reloads its models unless the model daemon (5.2.0+) is warm, so recall time depends on the version. The latency fields in the stored report files are inconsistent (recall and answer p50 are swapped in some files), so they are not quoted here.
- **Single conversation.** Per-cell noise is about +/-0.1. The F regression and the D gain exceed that; the +0.01 overall from B does not (its per-category gains are within noise too).
- **Judge.** Pass `--judge-model opus` for numbers comparable to the headline. The default (`sonnet`) grades about 0.07 lower.
