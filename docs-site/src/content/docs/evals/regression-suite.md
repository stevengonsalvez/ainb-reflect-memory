---
title: Regression suite
description: The layered tests that keep reflect's recall, storage backends and plugin packaging from silently drifting, what each layer proves, what runs in CI, and how to run it yourself.
sidebar:
  order: 2
---

reflect has to stay provably stable while its recall features and storage backends evolve. The suite answers two questions: did the Postgres backend change what recall returns, and do the 57 ported recall features (the 4.1.0 "ports") still behave? It is built in layers, from fast deterministic unit tests to heavy full-stack golden runs. Only the cheap layers gate pull requests.

```text
┌───────────────────────┐  ┌──────────────────────┐  ┌───────────────────────┐
│ Unit + contract tests │  │ Behavioral proofs    │  │ Golden snapshot diff  │
│ tests/, CI gate       │  │ 60 files, real engine│  │ R@5 vs baseline.json  │
│ slim install, fast    │  │ manual / non-blocking│  │ manual (workflow_disp)│
└───────────────────────┘  └──────────────────────┘  └───────────────────────┘
┌───────────────────────┐  ┌──────────────────────┐  ┌───────────────────────┐
│ Backend parity        │  │ e2e (Playwright)     │  │ Plugin contract       │
│ local vs Postgres     │  │ reflect serve UI     │  │ manifests + assets    │
│ needs a database      │  │ CI gate              │  │ CI gate               │
└───────────────────────┘  └──────────────────────┘  └───────────────────────┘
```

## What runs where

| Layer | Location | What it proves | Needs | Runs in |
|---|---|---|---|---|
| Unit tests | `tests/test_*.py`, `tests/postgres/` (no-DB files) | Frontmatter schema, write flow, metrics, model daemon, fleet import, domain boost, recency, issues pipeline, `reflect serve` curation, Postgres SQL builders and models | `[dev]` install only | CI job `test`, Python 3.11 and 3.12 (**gate**) |
| Fleet guards | `test_fleet_importer.py`, `test_domain_boost.py`, `test_fleet_context_format.py`, `test_recency_norm.py` | Fleet importer and recall ranking inputs stay deterministic | `[dev]` install | CI job `fleet-guards` (**gate**) |
| Fleet proofs F1 to F3 | `tests/eval/behavioral/proofs/proof_F*.py` | Fleet ingest isolation, domain boost ranking, quarantine enforcement against the real engine | `[graph]` + model | Same job, **non-blocking** (skips or fails on slim) |
| e2e | `tests/e2e/` (Playwright) | Every [memory browser](/ainb-reflect-memory/guides/memory-browser/) flow against a fixture KB | Node 20, Chromium | CI job `e2e` (**gate**) |
| Plugin contract | `scripts/check_plugin_contract.py`, manifest JSON checks | Manifests are valid JSON with name and version; every `${CLAUDE_PLUGIN_ROOT}` path exists (and shell scripts are executable); files external consumers rely on (for example the statusline's `plugin/scripts/reflect_timeline.sh`) are still at their contracted paths | stdlib | CI job `plugin-manifest` (**gate**) |
| Behavioral proofs | `tests/eval/behavioral/proofs/` | Each ported feature has an observable, deterministic invariant on a real-engine KB | `[graph]`, embedding model, full-stack `reflect` | **Manual** |
| Golden snapshot diff | `tests/eval/snapshot_diff.py`, `tests/eval/results/baseline.json` | Ranking quality on 20 golden queries has not regressed | `[graph]` + model | CI job `golden-diff`, **`workflow_dispatch` only**, never blocks a PR |
| Backend parity | `tests/postgres/nanographrag/` | Local and Postgres backends return identical evidence | Live Postgres with pgvector | **Manual** (skips cleanly without a database) |
| Plugin tests | `plugin/tests/`, `plugin/adapters/tests/` | Drain, hooks, recall scripts, cascade, adapters | `[dev]` install; some tests shell out to `uv` and the recall script, so they are slow on a cold machine | **Not run by any CI workflow** (see [gaps](#known-gaps)) |
| LOCOMO benchmark | `tests/eval/locomo/` | Answer quality, not regression | Heavy, costs money | Manual, see [benchmarks](/ainb-reflect-memory/evals/benchmarks/) |

At the time of writing, the CI gate command passes locally on a slim install with `278 passed, 18 skipped` (the skips are database-backed and graph-stack tests that need extra dependencies).

## Run it

```bash
# CI gate, slim install (no torch, no models)
uv venv --python 3.12 && uv pip install -e '.[dev]'
uv run pytest -q tests --ignore=tests/eval

# Plugin-side tests (not in CI)
uv run pytest -q plugin/tests plugin/adapters/tests

# Packaging contract
python3 scripts/check_plugin_contract.py

# Memory browser e2e (starts reflect serve against a fixture copy)
cd tests/e2e && npm install && npx playwright install chromium && npx playwright test
```

Heavy layers need the full stack. Use a venv with the graph extra and point the harness at its `reflect`:

```bash
uv venv .venv-eval --python 3.12
VIRTUAL_ENV=$PWD/.venv-eval uv pip install -e '.[dev,graph]'
export RECALL_EVAL_BIN_DIR=$PWD/.venv-eval/bin    # so recall.py and the harness resolve the same CLI

# the behavioral proofs, one pytest process per file, writes a verdict matrix
python3 tests/eval/behavioral/run_proofs.py            # all
python3 tests/eval/behavioral/run_proofs.py R7 R8      # only those ports

# golden snapshot gate (exit 1 on regression, exit 0 and SKIP when the env cannot run)
python3 tests/eval/snapshot_diff.py
python3 tests/eval/snapshot_diff.py --update-baseline  # only after an intentional ranking change

# backend parity, needs Postgres with the pgvector extension
DATABASE_URL=postgresql://... uv run pytest -m integration tests/postgres
```

:::caution
The proofs and the golden run build a real index (`reflect reindex --force`) in a throwaway KB. They are slow and use the embedding model (about 420 MB, cached under your Hugging Face home). Do not run them against your real `~/.learnings`; the harness isolates `GLOBAL_LEARNINGS_PATH`, `REFLECT_STATE_DIR` and `XDG_CACHE_HOME` to temp directories for you.
:::

## The 13 lookup types

Recall combines several lookups. Only the ones that read nano-graphrag's storage can be affected by the Postgres backend.

| # | Lookup | Layer | Backend-coupled | Source |
|---|---|---|---|---|
| 1 | Vector / semantic (naive) | nano-graphrag | yes (local vector store vs pgvector) | `src/reflect_kb/cli/graph_engine.py` |
| 2 | Graph-local (entity neighborhood) | nano-graphrag | yes | `graph_engine.py` |
| 3 | Graph-global (community reports) | nano-graphrag | yes | `graph_engine.py` |
| 4 | BM25 / lexical (QMD) | QMD | no (own index) | `plugin/skills/recall/scripts/recall.py` |
| 5 | Typed-link graph (R1) | nano-graphrag + recall | yes | `src/reflect_kb/cli/graph_links.py` |
| 6 | Cross-encoder rerank (R2) | recall | no | `src/reflect_kb/recall/cross_encoder.py` |
| 7 | Embed + MMR diversity (R3) | recall | no (own embed call) | `reflect embed` in `src/reflect_kb/cli/learnings_cli.py` |
| 8 | Temporal (R5, R6) | recall | no | `recall.py` |
| 9 | Entity / alias lookup | nano-graphrag | yes | `src/reflect_kb/cli/entity_store.py` |
| 10 | Corpus saved-filter (M7) | recall | no (frontmatter scan) | `src/reflect_kb/recall/corpus.py` |
| 11 | RRF fusion + recency / confidence / tag rerank | recall | no | `recall.py` |
| 12 | Staged 3-layer recall (M1) | recall | no | `plugin/skills/recall/scripts/recall_stages.py` |
| 13 | Per-project sharding / global scope (R15, R16) | recall | no | `recall.py` |

The backend swap is inert unless **both** `REFLECT_PG_DSN` and `REFLECT_WORKSPACE_ID` are set (the generic `DATABASE_URL` does not trigger it). Of the 57 ported features, exactly one (R1, the graph arm) routes through nano-graphrag's storage; the other 56 are recall-layer and backend-agnostic. `tests/postgres/test_recall_backend_independence.py` pins that by scanning the recall script for references to the Postgres backend (`REFLECT_PG`, `reflect_kb.postgres`, the Pg storage classes, `pgvector`, `psycopg`) and failing if any appear. It needs no database.

## Behavioral proofs

Each proof file under `tests/eval/behavioral/proofs/` follows one pattern: seed specific learnings into a hermetic real-engine KB, run `recall.py` the way SessionStart does, and assert an observable invariant on ranking, inclusion, exclusion or metadata. No LLM takes part in the assertion, so the seeds and flags fully determine the result.

There are **60** proof files. 57 are the recall-upgrade ports, plus 3 fleet proofs:

| Family | Files | Theme |
|---|---|---|
| `R` | 17 | Retrieval: R1 to R16 and R20 (graph arm, rerank, MMR, token budget, temporal, OOD gate, bounded boosts, fuzzy cache, tiered inject, per-arm thresholds, sharding, project affinity, skills index) |
| `S` | 10 | Storage structuring (structured fields, typed links, numeric confidence, provenance, belief revision, history, chunk-hash dedup) |
| `SG` | 8 | Signals (contradiction, git events, idle sweep, test outcomes, loop detection, knowledge gaps, todo completion, permission replies) |
| `M` | 8 | claude-mem style safeguards (staged recall, writer breaker, quota abort, modes, commit verification, private-tag strip, corpus Q&A, token economics) |
| `A` | 6 | agentmemory style (pinned slots, bitemporal edges, TTL forget, followup diagnostic, synthetic compression, branch isolation) |
| `C` | 5 | Consolidation (semantic dedup, auto-consolidation, graph maintenance, lifecycle events, export/import) |
| `O` | 3 | Open-domain (observations layer, conventions doc, persona fields) |
| `F` | 3 | Fleet (ingest isolation, domain boost, quarantine) |

The feature behind each family is described on the [retrieval features](/ainb-reflect-memory/concepts/retrieval-features/) page and the 4.1.0 entry of the [changelog](/ainb-reflect-memory/changelog/).

`run_proofs.py` runs each file in its own pytest process (a crash in one cannot poison the rest), takes the verdict from the pytest return code (0 pass, 1 test failure, anything else error), writes `tests/eval/behavioral/results/matrix.json` and prints a markdown table. The `matrix.json` committed in the repo covers only 11 ports, so it is a sample from an earlier run, not a current full result.

## Golden snapshot diff

`tests/eval/snapshot_diff.py` builds a hermetic KB from `tests/eval/fixtures/corpus/`, scores the 20 queries in `tests/eval/fixtures/golden_queries.yaml`, and compares the result with the committed `tests/eval/results/baseline.json`.

| Query class | Count | Purpose |
|---|---|---|
| `exact` | 8 | Unique terminology; vector and BM25 should hit directly |
| `graph` | 5 | Best answer is one entity hop away from the lexical match (exercises R1) |
| `temporal` | 3 | Current convention must outrank the superseded one |
| `ood` | 4 | Nothing relevant exists; returned results count as noise |

The gate fails (exit 1) when either:

- overall recall at 5 drops by more than 0.05 against the baseline, or
- an `exact` query that had a relevant document in the baseline top 5 no longer has that document in its top 5.

The committed baseline records recall at 5 of 1.0, MRR of 0.9375 and a noise rate of 0.2 across the 20 queries (the four `ood` queries all return results, because `recall.py` leaves the OOD gate off unless `--min-overlap` or `REFLECT_RECALL_MIN_OVERLAP` is set). If the environment cannot run the real engine or no baseline exists, the script prints a SKIP and exits 0 instead of failing. The same check is available as a pytest test (`test_no_recall_regression`) that skips under the same conditions.

## Backend parity (Postgres)

`tests/postgres/nanographrag/test_backend_parity.py` seeds an identical corpus into the local-default backend and the Postgres backend (same pinned embedding, canned LLM extraction), runs `naive`, `local` and `global` queries, and asserts that the **evidence set is identical** on both. A second test asserts the local backend writes a `.graphml` file while the Postgres backend writes none. `test_backend_parity_realmodel.py` repeats the parity check with the real all-mpnet-base-v2 embedding model and skips unless sentence-transformers is installed.

:::note
Parity is asserted at evidence-set level, not byte level. The two ANN engines may order equal-scoring items differently, but reflect re-ranks downstream (RRF, MMR, cross-encoder), so tie order does not reach the user. That is expected, not a regression.
:::

The rest of `tests/postgres/` covers the adapters themselves:

| File | Proves |
|---|---|
| `test_server_is_dumb.py` | Storage adapters do no LLM or embedding work (source scan, no database) |
| `test_sql_builders.py`, `test_models.py`, `test_normalize.py` | `workspace_id` is always the first bound parameter and values are never interpolated; tenant scope is mandatory; dedupe hashing is stable (no database) |
| `test_integration_store.py` | Insert and full-text search, idempotent ingestion, graph neighborhood, tenant isolation, RLS fail-closed (live database) |
| `nanographrag/test_pg_storage_conformance.py` | Adapters satisfy nano-graphrag's storage contracts across two isolated instances |
| `nanographrag/test_cross_machine_graphrag.py` | A real GraphRAG pipeline inserts on "machine A" and answers `local` and `naive` queries on a fresh "machine B" from Postgres |
| `nanographrag/test_ng_rls.py` | Row-level security fails closed and isolates workspaces |

Database tests read `DATABASE_URL` or `REFLECT_TEST_DATABASE_URL`, apply the two migrations in `supabase/migrations/`, and skip with a clear message when no database, `psycopg` or pgvector is available.

## Known gaps

- **`plugin/tests/` and `plugin/adapters/tests/` are not run by CI.** The CI test job runs `pytest -q tests --ignore=tests/eval`, and no other workflow invokes the plugin test directories. That includes the drain regression tests behind the fixes in [5.2.1 to 5.2.5](/ainb-reflect-memory/changelog/), so run them locally before release.
- **Behavioral proofs are not a PR gate.** Only F1 to F3 run in CI, and non-blocking. The other 57 rely on a manual run with the full stack.
- **The golden diff is manual** (`workflow_dispatch`) because it installs about 2 GB of torch plus a model download.
- **Ruff and mypy are advisory.** Both run in CI with `continue-on-error` while a style and typing backlog is cleared.

## Adding coverage

- A new recall feature should ship a proof named `proof_<PORT>_<slug>.py` under `tests/eval/behavioral/proofs/`; `run_proofs.py` derives the port id from the filename.
- A fix to the drain or hooks should include a test under `plugin/tests/` that drives the real script against a stub `claude`, as the 5.2.x tests do.
- If an intentional change shifts golden-query ranking, regenerate the baseline with `--update-baseline` and commit it with an explanation.
