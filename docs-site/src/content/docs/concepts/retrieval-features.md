---
title: Retrieval features
description: Every optional recall feature in reflect with its default (on or off), config key, cost and latency impact, and what breaks without it.
sidebar:
  order: 6
---

Each stage of the [recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/) is a separate feature with its own switch. This page lists them all: default state, the exact key, what it costs, and a short note on what it buys. Defaults were read from `recall.py` and confirmed by importing it with a clean environment.

:::note[Try it first]
The [Recall walkthrough](/ainb-reflect-memory/interactive/recall-walkthrough/) steps through these stages on a sample query.
:::

## How the switches work

- Almost every switch is an **environment variable** read once when `recall.py` starts. Set them in your harness's env block (for Claude Code, the `env` stanza of `settings.json`) and the hooks inherit them.
- Boolean gates are "on unless the value is `0`": `RECALL_MMR=0` disables, anything else (or unset) enables. Numeric values that fail to parse fall back to the default, so a typo cannot crash the always-on path.
- CLI flags on `recall.py` (and so on `/reflect:recall`) override the env for one call.
- The `[recall.*]` keys in [`plugin/reflect.toml`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/reflect.toml) (`cross_encoder`, `boost`) and the `recall.arm.*` defaults in `reflect_config.py` are declarative only. Nothing in the shipped scripts reads them to drive recall: `recall.py` reads the `RECALL_*` env vars directly. Treat the TOML keys as documentation of intent, and set the env vars.

## Summary

Cost is relative to a recall that uses none of the optional stages. "Subprocess" means one extra `reflect` or `qmd` process, running in parallel where noted.

| Feature | Default | Key | Cost / latency |
|---|---|---|---|
| BM25 arm (QMD) | on if `qmd` installed | none (presence of `qmd`) | one subprocess, 10 s cap, parallel with the others |
| Graph arm (R1) | on | `RECALL_GRAPH_ARM` | one `reflect search --mode local`, 60 s cap, parallel |
| Temporal arm (R5) | on, only fires on a date phrase | `RECALL_TEMPORAL_ARM` | reads up to 5000 note files when it fires |
| Date parsing (R6) | on | `RECALL_TEMPORAL` | sub-millisecond regex |
| Cross-encoder rerank (R2) | on | `RECALL_CROSS_ENCODER`, `RECALL_CE_TIMEOUT` (60) | one subprocess over the top 20, needs the `[graph]` extra |
| MMR diversity (R3) | on | `RECALL_MMR`, `RECALL_MMR_LAMBDA` (0.7), `RECALL_EMBED_TIMEOUT` (60), `--no-mmr` | one embed subprocess, concurrent with the cross-encoder |
| Token budget (R4) | off | `--max-tokens`, `REFLECT_RECALL_MAX_TOKENS` (SessionStart) | none |
| OOD gate (R7) | off in `recall.py`, 0.2 at SessionStart | `--min-overlap`, `REFLECT_RECALL_MIN_OVERLAP` | none (string matching on 5 notes) |
| Per-arm floors (R12) | off (all 0) | `RECALL_ARM_{VECTOR,BM25,GRAPH,TEMPORAL}_MIN_SCORE` | none |
| Bounded boosts (R8) | on | `RECALL_{CONFIDENCE,RECENCY,TAG,PROOF}_ALPHA` | none |
| Project affinity (R16) | on (0.2) | `RECALL_PROJECT_ALPHA` | one `git` call, memoised |
| Domain affinity (F3) | on, inert without a hint | `RECALL_DOMAIN_ALPHA`, `--domain-hint` | none |
| Authority tier (F3) | on (0.1) | `RECALL_AUTHORITY_ALPHA` | none |
| Speculative down-rank (SG3) | on (0.2) | `RECALL_SPECULATIVE_ALPHA` | none |
| Exact cache | on, 1 h | `--no-cache`, `--cache-ttl` | saves the arms, cross-encoder and embeddings on a hit |
| Fuzzy cache (R9) | on | `RECALL_FUZZY_CACHE`, `RECALL_FUZZY_THRESHOLD` (0.85) | index read, up to 200 entries |
| Project and branch shards (R15, A6) | on, but shards must exist | `--global` / `RECALL_GLOBAL`, `--all-branches` / `RECALL_ALL_BRANCHES`, `RECALL_BRANCH`, `RECALL_LEARNINGS_ROOT` | smaller corpora when shards exist |
| Persona answer (O3) | on | none | one SQLite lookup, can skip the whole pipeline |
| Tiered inject (R10) | off | `REFLECT_TIERED_INJECT`, `REFLECT_SKILL_TIER_MIN_SCORE` (2.0) | one SQLite lookup at SessionStart |
| Short-circuit probe (R11) | on, no flag | thresholds in code (0.8 score, 30 days) | one extra `recall.py --limit 1` at SessionStart, reuses the cache |
| Memory slots (A1) | off | `REFLECT_SLOTS` | one SQLite read, up to 4000 chars |
| Conventions pointer (O2) | off (rides tiered inject) | `REFLECT_TIERED_INJECT`, `REFLECT_CONVENTIONS_SYMLINK` | one SQLite read, one line |
| Staged recall (M1) | on demand | `recall_stages.py index / timeline / hydrate` | token-cheap by design |
| Token economics (M8) | on | `RECALL_ECONOMICS` | string math, adds a per-row suffix |
| Field projection (S1) | off | `--field NAME` | shrinks output |
| HyDE query expansion | off | `REFLECT_RECALL_HYDE=1`, `REFLECT_DRAIN_MODEL` | one headless `claude -p` call per recall, 60 s cap |
| Knowledge-gap log (SG6) | on | `RECALL_GAP_LOG`, `--no-gap-log` | one file append on empty results |
| Follow-up diagnostic (A4) | on | `RECALL_FOLLOWUP`, `RECALL_FOLLOWUP_WINDOW_SECONDS` (30), `--no-followup` | small state file write |
| Corpus Q&A (M7) | on demand | `recall.py --corpus NAME` / `/reflect:corpus` | no models loaded |
| Fleet-context output (F3) | on demand | `--format fleet-context`, `--include-quarantined` | hermes shim only |
| Bitemporal edge filter (A2) | helper only | `RECALL_BITEMPORAL_EDGES` | not called by `recall()` |

## Retrieval arms

### BM25 arm (QMD)

Runs `qmd search QUERY -c learnings` and reads the matching note files. It has no flag: it is active exactly when `qmd` is on `$PATH`. It catches literal tokens (a file name, an error string) that embeddings blur. Without it, the vector and graph arms still work, but exact identifiers rank worse.

### Graph arm (R1)

`RECALL_GRAPH_ARM=0` disables. It runs the engine in `local` mode (entity neighbourhood in the nano-graphrag graph), so a note linked by an edge to a lexical hit can surface even when it shares no words with the query. Example: a hit on "recalcTax is idempotent but expensive" pulls in the linked note explaining the rounding bug behind the double call. Skipped when you already ask for `--mode local`. Without it, only notes that match the query text come back.

### Temporal arm (R5) and date parsing (R6)

`RECALL_TEMPORAL=0` stops date extraction, `RECALL_TEMPORAL_ARM=0` stops only the arm. When the query has a phrase such as "last week" or "since 2026-01-01", the arm scans the shard's `documents/` for notes whose timestamp (`archived`, `updated_at`, `created`, `date` frontmatter, else the archive header) falls in the window. Candidates rank by overlap with the date-stripped query, then by closeness to the window midpoint. Notes with no parsable date are never guessed into a window. Date-free queries get nothing from this arm.

The JSON output (`--format json`) carries the parsed range under `temporal`.

## Ranking

### Cross-encoder rerank (R2)

`RECALL_CROSS_ENCODER=0` disables; `RECALL_CE_TIMEOUT` (default 60 s) bounds the call; `REFLECT_CE_MODEL` swaps the model (default `cross-encoder/ms-marco-MiniLM-L-6-v2`). It rescores the top 20 fused candidates by reading query and note together, then multiplies that score with the boosts. Example: for "flaky test in the auth suite", a keyword-heavy note about "auth token format" drops below "auth integration test is flaky under parallel xdist".

Needs `sentence-transformers` (the `[graph]` extra); a slim install degrades to the boost formula alone. Because hooks pin `HF_HUB_OFFLINE=1`, the model has to be cached already. Run a recall once from a shell (for example `/reflect:recall`) so the download happens outside a hook.

### MMR diversity (R3)

`RECALL_MMR=0` or `--no-mmr` disables; `RECALL_MMR_LAMBDA` or `--mmr-lambda` sets the relevance versus diversity trade-off (1.0 is pure relevance, 0.0 pure diversity; default 0.7). It selects the final `limit` notes so near-duplicates do not fill the slots: four notes saying "raise nginx worker_connections" collapse to one, leaving room for the distinct "enable upstream keepalive" note. Slim installs without embeddings fall back to a plain top-k slice.

### Bounded boosts (R8) and affinity boosts

Confidence, recency, tags and proof count (R8, S3, S4), project affinity (R16), domain affinity and authority tier (F3), and the speculative down-rank (SG3) all use one shape, `1 + alpha * (norm - 0.5)`. Each factor can move a score by at most plus or minus alpha/2, so they break ties and never override a decisive relevance gap. The full table of alphas and norms is in [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/#6-scoring). Set any alpha to `0` to remove the factor.

Two behaviours that are easy to miss:

- Project affinity only lifts same-project notes (up to +10%); foreign notes are never pushed below their base score. It is neutralised when recall is already scoped to a single-project shard.
- Domain affinity does nothing unless the caller passes `--domain-hint DOMAIN` and the note has a matching `domain` frontmatter value.

## Gates and budgets

### OOD gate (R7)

`--min-overlap F` (or `REFLECT_RECALL_MIN_OVERLAP`) empties the result when the best of the top 5 notes contains less than fraction `F` of the query's content words. Default is 0 (off) in `recall.py`; the SessionStart hook turns it on at 0.2 because most sessions have no relevant prior art and junk costs context. The UserPromptSubmit hook does not pass the flag, so it follows `REFLECT_RECALL_MIN_OVERLAP` (0 if unset). Without a gate the engine's nearest neighbours are injected even for a query the KB has never seen.

### Per-arm floors (R12)

The arms' native scores are not comparable, so R12 drops weak candidates from each arm by its own floor on query-term coverage, before fusion. Env vars: `RECALL_ARM_VECTOR_MIN_SCORE`, `RECALL_ARM_BM25_MIN_SCORE`, `RECALL_ARM_GRAPH_MIN_SCORE`, `RECALL_ARM_TEMPORAL_MIN_SCORE`, each clamped to 0..1.

All four default to **0 (off)**. The calibrated values the config module lists (vector 0.1, BM25 0.15, graph 0, temporal 0.05) are suggestions that `recall.py` does not apply on its own, because a floor changes what the OOD gate sees. Derive floors for your corpus with:

```bash
uv run plugin/skills/recall/scripts/recall.py --calibrate-thresholds
```

It samples up to 40 notes, probes each arm with a note's own title and key insight, and prints TOML plus `export RECALL_ARM_*` lines at the 10th percentile of in-domain coverage. The graph arm stays open (a related note can share zero query terms).

### Token budget (R4)

`--max-tokens N` keeps ranked notes until the estimate (characters divided by 4) would pass `N`; the first note is always kept. Default 0 (off). Only SessionStart wires it to an env var, `REFLECT_RECALL_MAX_TOKENS`, also default 0, so `--max-chars` is the effective cap unless you set it.

## Caching and scope

### Exact and fuzzy cache (R9)

The exact cache is on with a 1 h TTL (`--no-cache`, `--cache-ttl`). The fuzzy tier (`RECALL_FUZZY_CACHE=0` disables) reuses a cached fetch when the new query's token set is within Jaccard 0.85 of an earlier one (`RECALL_FUZZY_THRESHOLD`). "How do I debounce search input" and "debouncing the search-as-you-type box" share a fetch. Both are invalidated when the KB changes. Details in [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/#2-cache-r9).

### Project and branch shards (R15, A6)

Recall reads `~/.learnings/shards/<project>/branches/<branch>/`, then the project shard, then falls back to the pooled `~/.learnings`. Flags: `--global` / `RECALL_GLOBAL=1` (pooled across projects), `--all-branches` / `RECALL_ALL_BRANCHES=1`, `RECALL_BRANCH` (override branch), `RECALL_LEARNINGS_ROOT` (move the root). `main`, `master` and a detached HEAD map to the project shard.

A search of `src/` and `plugin/` finds nothing that creates shard directories; only the read side exists. Until you populate them, every recall falls back to the pooled KB and this feature has no effect. See the caution in [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/#1-scope-which-kb-gets-read-r15-a6).

## SessionStart tiers

### Tiered inject (R10) and short-circuit (R11)

`REFLECT_TIERED_INJECT=1` makes SessionStart consult the skills index (the `skills` table in `reflect.db`) first. Skill hits are scored 2.0 for a name or tag token match and 1.0 for a summary match; `REFLECT_SKILL_TIER_MIN_SCORE` (default 2.0) sets the bar, and up to 2 skills are injected as name plus one-line summary. A strong hit wins outright and the raw-learnings recall is skipped. The flag also turns on the conventions pointer (O2): one line with a summary and path of a fresh `CONVENTIONS.md`, never its body, and nothing when the doc is stale. `REFLECT_CONVENTIONS_SYMLINK=1` additionally symlinks it into the project root.

The R11 probe is separate and has no flag. SessionStart always runs one `recall.py --limit 1 --confidence HIGH --format json` first. If that top note is fresh (its `<!-- archived -->` header at most 30 days old) and its confidence-derived score is above 0.8, only that note is injected and the full recall never runs. In practice this means a recent HIGH-confidence note. The probe shares its cache fetch with the full recall, so the extra cost when it does not short-circuit is small.

### Memory slots (A1)

`REFLECT_SLOTS=1` injects pinned, agent-editable scratchpad slots (per project, seeded with 8 defaults) ahead of everything else, up to 4000 characters. They are working memory, not retrieval, so they are prepended to whatever the rest produces and never suppress it. Manage them with `/reflect:slots`.

## Output shape

### Staged recall (M1)

For deep digs, `recall_stages.py` splits recall into three token-cheap steps instead of one full dump:

```bash
uv run plugin/skills/recall/scripts/recall_stages.py index "tokio panic on shutdown" --limit 20
uv run plugin/skills/recall/scripts/recall_stages.py timeline --anchor <ID> --depth-before 3 --depth-after 3
uv run plugin/skills/recall/scripts/recall_stages.py hydrate <ID> [<ID> ...]
```

`index` returns id, title, score, project and date (about 50 to 100 tokens per hit, default limit 20). `timeline` shows chronological neighbours of an anchor (default depth 3 each way). `hydrate` returns full bodies plus entity sidecars for the ids you picked, about 500 to 1000 tokens each. There are no `reflect index` or `reflect hydrate` CLI subcommands; the script is the interface.

### Token economics (M8)

On by default. Each row gets a glyph from the active mode plus `D:<discovery> -> R:<read> (-pct%)`, the header rolls up totals, and SessionStart appends a `memory:` footer. Discovery cost comes from the note's `discovery_tokens` frontmatter, else its provenance, else a per-type average (bug-fix 3000, anti-pattern 2500, correction 2000, pattern 1500, decision 1200; default 1500). `RECALL_ECONOMICS=0` restores the plain format. See [Token economics](/ainb-reflect-memory/interactive/token-economics/).

### Field projection (S1)

`--field rule` (or `fix`, `root_cause`, `problem`) returns just that frontmatter field per hit, falling back to the matching body section for older notes. It is the cheapest way to inject a rule.

### Persona answer (O3)

Always on. An aggregate-shaped query answered by a high-confidence `project_persona` field returns that one line and skips the pipeline.

## Opt-in and diagnostic features

### HyDE query expansion

`REFLECT_RECALL_HYDE=1` appends one invented answer sentence to the query before retrieval, generated by a headless `claude -p` call (model from `REFLECT_DRAIN_MODEL`, default `sonnet`, 60 s cap, falls back to the raw query on any failure). It is meant to help questions whose wording differs from how the fact was written. It is an LLM call on every recall, hooks included if the variable is in their environment, so enable it deliberately.

### Knowledge-gap log (SG6) and follow-up diagnostic (A4)

An empty result from a real query is appended to `~/.reflect/knowledge-gaps.jsonl` (`RECALL_GAP_LOG=0` disables). A second, different query within 30 seconds whose results are fully disjoint from the first counts as a follow-up, logged to the engine's `metrics.jsonl` for `/reflect:cost` (`RECALL_FOLLOWUP=0` disables; window `RECALL_FOLLOWUP_WINDOW_SECONDS`). SessionStart and SubagentStart pass `--no-gap-log` and `--no-followup` because their queries are synthetic.

### Corpus Q&A (M7)

`recall.py --corpus NAME --corpus-filter "tag:auth since:2026-01-01"` snapshots every matching learning into `~/.reflect/corpora/NAME.json` and prints a primed context document; `--corpus-rebuild` re-applies the saved filter. It loads no models. The `/reflect:corpus` skill drives it.

### Fleet-context output (F3)

`--format fleet-context` emits an authority-labelled block (at most 5 items, at most 2000 estimated tokens, `fleet-context/v1` marker) and includes quarantined notes unless told otherwise. The hermes shim in `plugin/adapters/hermes/shim/pre_llm_recall.py` uses it.

### Bitemporal edge filter (A2)

`recall.py` defines `filter_edges_by_tvalid()` and the `RECALL_BITEMPORAL_EDGES` gate, intended to restrict the graph arm to edges valid inside a query's date window. Nothing in the `recall()` path calls it, so the flag currently changes nothing at runtime.

## Model daemon

Not a recall feature but it governs latency. `reflect_kb/model_daemon.py` keeps the embedding model and cross-encoder warm behind a unix socket, auto-spawned on first use and shared across KBs. `REFLECT_NO_DAEMON=1` disables it, `REFLECT_IDLE_TIMEOUT` (default 1800 s) sets idle exit, `REFLECT_DAEMON_TIMEOUT` (default 120 s) bounds one client request. Without it each recall that reaches the cross-encoder or MMR loads the models in-process.

## Behavioural proofs

Each numbered feature has a proof under [`tests/eval/behavioral/proofs/`](https://github.com/stevengonsalvez/ainb-reflect-memory/tree/main/tests/eval/behavioral/proofs) (for example `proof_R2_cross_encoder_rerank.py`, `proof_R7_ood_gate.py`). The proofs seed notes and assert the observable outcome with the feature engaged. See [Regression suite](/ainb-reflect-memory/evals/regression-suite/).
