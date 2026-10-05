---
title: Retrieval styles
description: Every retrieval style reflect has built, tried or declined (lexical, vector, graph, hybrid RRF, rerank, MMR, query expansion, temporal, entity, sharding), what each does, where it lives, what was measured and its status.
sidebar:
  order: 7
---

A map of the retrieval strategies in reflect, by style rather than by feature id. For each: what it does, where the code is, what has been measured, and whether it is live. The per-switch defaults and costs are on [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/); how the stages chain together on one query is on [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/). The only quality numbers come from the [LOCOMO pilot](/ainb-reflect-memory/evals/locomo/) and the small [recall eval](/ainb-reflect-memory/evals/benchmarks/); where a style has no measurement, this page says so.

Status values: **live** (runs by default), **opt-in** (shipped, off by default), **helper** (code exists, not called by `recall()`), **dropped** (tried, removed or abandoned), **not built** (planned or considered, no code).

## Overview

```text
                     query
                       │
     ┌────────┬────────┴────────┬──────────┐        4 arms, parallel thread pool
     ▼        ▼                 ▼          ▼
  vector    BM25            graph        temporal
 (naive)   (QMD)           (local)     (date window)
     └────────┴────────┬────────┴──────────┘
                       ▼
              per-arm floor (R12, off)  ──▶  RRF fusion (k=60)
                       ▼
        cross-encoder rerank × bounded boosts ──▶ MMR ──▶ OOD gate (R7) ──▶ budget
```

| Style | What it adds | Status | Measured impact |
|---|---|---|---|
| [Dense vector (GraphRAG `naive`)](#dense-vector) | semantic match | live | baseline arm |
| [Lexical BM25 (QMD)](#lexical-bm25) | exact tokens, error strings | live if `qmd` installed | no isolated measurement |
| [Graph neighbourhood (`local`)](#graph-neighbourhood) | one-hop connected notes | live | no isolated measurement |
| [Graph communities (`global`)](#graph-communities) | cluster-level context | helper, placeholder reports | none |
| [Typed-edge filtering](#typed-edges) | filter hops by link type | helper | none |
| [Hybrid fusion (RRF)](#hybrid-fusion-rrf) | merge arms without score calibration | live | none isolated |
| [Cross-encoder rerank](#cross-encoder-rerank) | meaning-level reorder | live | LOCOMO fix B (bundled) |
| [Bounded boosts](#bounded-boosts) | recency, confidence, tags, proof, project | live | none isolated |
| [MMR diversity](#mmr-diversity) | de-duplicate near-twins | live | none isolated |
| [Temporal](#temporal-retrieval) | date-window arm, query date parsing | live | LOCOMO temporal 0.80 (not isolated) |
| [Entity-aware](#entity-aware-retrieval) | entities at index time, not query time | index-time only | none |
| [Query expansion (HyDE)](#query-expansion-hyde) | answer-shaped query | opt-in | LOCOMO +0.06 overall |
| [Embedder swap](#embedder-and-reranker-swap) | stronger vectors | opt-in | LOCOMO fix B |
| [Gates and floors](#gates-and-floors) | return nothing when nothing fits | R7 off in `recall.py`; R12 off | LOCOMO regression at 0.15 |
| [Caches](#caches) | skip repeat work | live | none |
| [Scoping and sharding](#scoping-and-sharding) | project and branch isolation | live where shards exist | none |
| [Staged, tiered, persona, slots](#staged-and-tiered-recall) | cut tokens, not rank | mixed | none |
| [Shared Postgres store](#shared-postgres-store) | same vectors on every machine | opt-in | parity tests only |
| [Not built or declined](#not-built-or-declined) | | | |

## Dense vector

**What.** nano-graphrag's `naive` mode: embed the query, nearest-neighbour search over the chunk vectors. It is the default primary arm (`DEFAULT_MODE = "naive"`).

**Where.** `LearningsGraphEngine.search(mode=...)` in [`src/reflect_kb/cli/graph_engine.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/src/reflect_kb/cli/graph_engine.py); recall calls it as `reflect search QUERY --mode naive --format json`. Default embedder `all-mpnet-base-v2` (768-d, CPU), set in [`model_daemon.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/src/reflect_kb/model_daemon.py) and overridable with `REFLECT_EMBED_MODEL`. Since 5.2.0 the model lives in a persistent unix-socket daemon, so a warm embed or rerank round trip is about 0.2 s instead of 7.5 s cold (from the 5.2.0 changelog).

**Status.** Live. In the committed recall eval (`tests/eval/results/baseline.json`) every attributed top-5 slot came from the vector arm (`graphrag_only` 48, `qmd_only` 0, `both` 0, `neither` 8); QMD most likely was not installed in that environment (inference).

## Lexical BM25

**What.** QMD's `qmd search`, a BM25 index over the same markdown files. It catches literal tokens (a file name, an error string) that embeddings blur.

**Where.** `fetch_qmd` in [`plugin/skills/recall/scripts/recall.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/skills/recall/scripts/recall.py). It is active exactly when `qmd` is on `$PATH`; a missing or failing `qmd` returns no results and recall continues. `reflect reindex` runs `qmd update` and `qmd embed` only if `qmd` is installed.

**Why `search` and not `query`.** The commit that introduced it (2026-04-23, `e80992567`) records the choice: `qmd query` runs LLM query expansion plus reranking and took about 25 s, while `qmd search` is BM25 only at about 0.45 s. Recall wanted the cheap complementary signal, and does its own reranking. QMD's own vector and LLM-rerank modes are therefore not used by recall (the TUI learnings screen does use `qmd query`; see [TUI learnings plugin](/ainb-reflect-memory/guides/tui-learnings-plugin/)).

**Status.** Live when installed. No quality number isolates BM25: the LOCOMO calibration run recorded a BM25 sample of 0 (`arm_thresholds_bge.txt`), consistent with QMD being absent there (inference).

## Graph neighbourhood

**What.** nano-graphrag's `local` mode walks the entity neighbourhood in the stored graph, so a note linked to a lexical or vector hit can surface even when it shares no words with the query (R1).

**Where.** Same `search` call with `--mode local`, added as a second arm in `recall()` unless the primary mode is already `local`. Entities and relationships come from `.entities.yaml` sidecars written next to each note (`entity_store.py`); the graph is rebuilt by `reflect reindex`.

**How the graph is built.** nano-graphrag normally calls an LLM to extract entities. reflect replaces that call with a passthrough in `graph_engine._llm_complete` that feeds the sidecar entities straight in, so indexing makes no model calls. The graspologic dependency is replaced by a small networkx shim (`graspologic_shim.py`) to avoid its numba/llvmlite chain.

**Status.** Live (`RECALL_GRAPH_ARM`, default on). Behavioural proof `proof_R1_graph_expansion_arm.py` shows the two-hop example; there is no ablation of its quality.

## Graph communities

**What.** `global` mode retrieves community-level reports, meant for broad "what patterns exist" questions.

**Status: helper, with a caveat.** `recall.py --mode global` and `reflect search --mode global` work, but recall never uses them by default. Because indexing uses the passthrough LLM, the community reports are a fixed placeholder ("A group of related technical concepts and patterns", rating 5.0) rather than real summaries, so global retrieval carries no per-community content. This is read from the code (`graph_engine._llm_complete`), not measured. The Postgres backend tests do cover `global` parity between backends, which tests storage, not usefulness.

## Typed edges

**What.** Each sidecar relationship has a type (for example `solves`, `caused_by`, `requires`). The type survives into the graph as a `[type]` prefix on the edge description, and `graph_links.py` recovers it from the stored GraphML with the standard library only, so slim builds can still query by link type ("what enabled this fix?").

**Where.** [`src/reflect_kb/cli/graph_links.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/src/reflect_kb/cli/graph_links.py), exposed as `LearningsGraphEngine.get_typed_edges`.

**Status.** Helper. The module docstring describes filtering the R1 arm by link type, but `recall.py` does not import it, so the live graph arm does not filter by type today.

## Hybrid fusion (RRF)

**What.** Reciprocal-rank fusion over the arm result lists: `score = sum over arms of 1 / (60 + rank)`, de-duplicated by note id or chunk hash. Ranks, not scores, are fused because BM25, cosine and graph scores are not comparable.

**Where.** `rrf_fuse` and `RRF_K = 60` in `recall.py`; the four arms run in a `ThreadPoolExecutor(max_workers=4)` and a failed arm returns an empty list.

**History.** The first retrieval plan (April 2026, [Retrieval roadmap](/ainb-reflect-memory/design/retrieval-roadmap/)) proposed a weighted blend, `qmd_score * 0.6 + graph_degree * 0.4`. What shipped is RRF over BM25 and vector (commit `e80992567`), later widened to graph and temporal arms.

**Status.** Live. Not ablated against a weighted blend.

## Cross-encoder rerank

**What.** After fusion, the top 20 candidates are scored jointly with the query by a local cross-encoder (default `cross-encoder/ms-marco-MiniLM-L-6-v2`, `REFLECT_CE_MODEL` to swap). The score becomes the primary sort key; the boosts multiply it (R2).

**Where.** [`src/reflect_kb/recall/cross_encoder.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/src/reflect_kb/recall/cross_encoder.py) and the `reflect rerank` subcommand; `RECALL_CROSS_ENCODER=0` disables. Slim builds without `sentence-transformers` fall back to formula-only ordering.

**Measured.** Only bundled: LOCOMO fix B swapped both the embedder and the reranker (to bge) and gained +0.01 overall, with single-hop 0.70 to 0.80.

**Status.** Live.

## Bounded boosts

**What.** Secondary signals multiply the rerank score but each is clamped, so they break ties and cannot override a decisive relevance gap (R8, R16, F3, SG3). Defaults in `recall.py`: confidence, recency and tag alpha 0.2 each (about +/-10%), proof count 0.1, project affinity 0.2, domain 0.2, authority 0.1, speculative down-rank 0.2. Recency is a linear decay over 365 days with a floor of 0.1, and neutral 0.5 when the date is missing.

**History.** The first recall (v3.1.0) used `confidence x recency (60-day half-life) x tag overlap`. The R8 commit replaced the exponential decay, which crushed old notes to near zero, with the bounded linear form.

**Status.** Live. No ablation.

## MMR diversity

**What.** Maximal Marginal Relevance on the final top-k: keep the best hit, then pick each next note to maximise `lambda * relevance - (1 - lambda) * max similarity to those already picked`, lambda 0.7 (`RECALL_MMR_LAMBDA`). It stops three near-identical notes filling three slots. Similarity uses the same embedding space as the index (`reflect embed`).

**Where.** `mmr_select` in `recall.py`; `RECALL_MMR=0` or `--no-mmr` disables. It runs concurrently with the cross-encoder, so added latency is the larger of the two, not the sum.

**Status.** Live. Proof: `proof_R3_mmr_diversity.py`. No quality ablation.

## Temporal retrieval

**What.** Two parts. R6 parses natural-language dates in the query ("last week", "since 2026-01-01", "in March") into a range with a confidence. R5, only when a range was found, adds an arm that scans note timestamps for the window and feeds RRF. Date-free queries get nothing from this arm. An older design also considered bitemporal edge validity (A2); the filter exists as a helper and `recall()` does not call it.

**Where.** [`plugin/skills/recall/scripts/temporal_extraction.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/skills/recall/scripts/temporal_extraction.py), `fetch_temporal` in `recall.py`; `RECALL_TEMPORAL`, `RECALL_TEMPORAL_ARM`, `RECALL_BITEMPORAL_EDGES`.

**Measured.** LOCOMO temporal questions scored 0.80 in the best configuration, but LOCOMO queries are phrased as questions about stored note dates and the benchmark did not isolate the arm.

**Status.** Live (A2 helper).

## Entity-aware retrieval

reflect is entity-aware at **index time only**. Each note can carry an `.entities.yaml` sidecar (entities with types and descriptions, typed relationships); `auto_extract_entities` falls back to heuristics (backtick terms, frontmatter) when the writer produced none. These feed the graph that the `local` arm walks. There is **no reflect-side entity detection on the query**: recall does not parse entity names out of the question. nano-graphrag's own `local` mode matches the query embedding against entity descriptions and expands from there, which is the only query-time entity step. A fuzzy entity-by-name-or-alias lookup exists in the Postgres `MemoryStore` (`MemoryStore.lookup_entities` in `postgres/store.py`), but recall does not call it.

**Status.** Index-time live; query-time entity linking not built.

## Query expansion (HyDE)

**What.** One headless `claude -p` call writes a single invented answer sentence, which is appended to the query so retrieval embeds answer-shaped text. It bridges the vocabulary gap between a question and the statement that answers it.

**Where.** `_hyde_expand` in `recall.py`, gated by `REFLECT_RECALL_HYDE=1`; model from `REFLECT_DRAIN_MODEL` (default `sonnet`), 60 s timeout, any failure falls back to the raw query. No new API key. Costs about $0.02 per recall plus latency (REPORT.md).

**Measured.** The largest single gain in the LOCOMO pilot: 0.74 to 0.80 overall, multi-hop 0.70 to 0.80, open-domain 0.60 to 0.70, adversarial 0.80 to 0.90 (Opus judge, n=50).

**Status.** Opt-in. The hooks run `recall.py` as a child process and inherit the environment, so setting `REFLECT_RECALL_HYDE=1` in the harness env turns it on for every recall, including SessionStart, adding a model call to each.

## Embedder and reranker swap

**What.** `REFLECT_EMBED_MODEL` (for example `BAAI/bge-base-en-v1.5`) and `REFLECT_CE_MODEL` replace the default models. The embedding dimension is derived from the loaded model, but vectors are dimension-specific, so a swap needs `reflect reindex --force`.

**Measured.** LOCOMO fix B: single-hop 0.70 to 0.80, open-domain 0.50 to 0.60, +0.01 overall. Kept as a knob, not made the default.

**Status.** Opt-in.

## Gates and floors

| Gate | What it does | Status |
|---|---|---|
| OOD gate (R7) | if the top hit covers too few query terms, inject nothing | `--min-overlap` default 0 (off) in `recall.py`; SessionStart hook uses 0.2 |
| Per-arm floors (R12) | each arm drops sub-floor candidates before fusion | off (all 0); calibrated suggestions 0.1 vector, 0.15 BM25, 0 graph, 0.05 temporal; `recall.py --calibrate-thresholds` |
| Token budget (R4) | pack notes until a token budget, not a count | `--max-tokens`, `REFLECT_RECALL_MAX_TOKENS` |

**Dropped result.** In LOCOMO the abstention gate (R7 at 0.15) over-suppressed: 27 of 50 answers became "NOT MENTIONED" against 12 of 50 without it, and overall J fell to 0.44. It was dropped for that benchmark; the shipped SessionStart default is 0.2 with a different goal (do not inject junk into a new repo). A gentler value was never tuned.

## Caches

Exact cache (1 h, per query), fuzzy cache (R9, Jaccard similarity of 0.85 or more against recent queries, `RECALL_FUZZY_CACHE`), and a follow-up diagnostic (A4). They save repeated work and change no ranking; the LOCOMO harness passes `--no-cache` so they cannot affect scores.

## Scoping and sharding

Per-project shards (R15) and branch sub-shards (A6) keep recall to the current project and branch by default; `--global` and `--all-branches` union them; a project-affinity boost (R16) softly prefers the current project in global mode. An empty shard falls back to the global KB (5.0.2 fix). Live where shards exist.

## Staged and tiered recall

These reduce tokens rather than change ranking.

- **Staged recall (M1)**: `recall_stages.py index / timeline / hydrate` returns a token-capped index first, then full bodies only for chosen ids.
- **Tiered inject (R10, R11)**: at SessionStart, look up curated skills first; a strong, fresh hit short-circuits the raw recall. Off unless `REFLECT_TIERED_INJECT=1`.
- **Persona fields (O3)** and **memory slots (A1)**: direct answers and pinned text that bypass search.
- **Corpus Q&A (M7)**: snapshot a filtered set of notes and answer questions over it with no retrieval models.

Details and defaults: [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/).

## Shared Postgres store

An opt-in backend (`REFLECT_PG_DSN` plus `REFLECT_WORKSPACE_ID`) swaps the local vector and graph storage for tenant-scoped Postgres with pgvector, so every machine queries the same index. Only the vector and graph arms touch it; BM25, rerank, MMR, temporal and the boosts are backend-independent (pinned by `tests/postgres/test_recall_backend_independence.py`). Evidence is parity tests (the local and Postgres backends return the same evidence set), not a quality comparison. See [Shared Postgres backend](/ainb-reflect-memory/design/shared-postgres-backend/).

## Not built or declined

| Idea | Outcome |
|---|---|
| Weighted blend of BM25 and graph degree | replaced by RRF before it shipped |
| QMD hybrid `query` (LLM expansion plus rerank) inside recall | declined for latency (about 25 s vs 0.45 s) |
| Real LLM community summaries for `global` mode | not built; reports are placeholders |
| Query-time entity linking | not built |
| Helpfulness-feedback reranking (roadmap phase 6) | not built; recall only logs to an append-only JSONL and the A4 follow-up diagnostic exists |
| Retrieval exposed as an MCP tool | open question in the 2026-04-20 handover; not built |
| SQLite single-file vector backend | considered in the Postgres work, explicitly not built |
| Abstention gate at 0.15 for LOCOMO | tried and dropped |

## Verification notes

Code locations and defaults above were read from this repo at `main` (`48968dd`) and spot-checked: `RRF_K = 60`, `DEFAULT_MODE = "naive"`, `CE_CANDIDATES = 20`, MMR lambda 0.7, alphas, `RECENCY_WINDOW_DAYS = 365`, the passthrough LLM stub, `HyDE` gating. The `--calibrate-thresholds` entry point is a flag on `recall.py`, not a `reflect` subcommand. The 60 proof files in `tests/eval/behavioral/proofs/` demonstrate behaviour with each knob on and off; they do not measure retrieval quality.
