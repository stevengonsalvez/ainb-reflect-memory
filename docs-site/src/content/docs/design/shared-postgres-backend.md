---
title: Shared Postgres backend
description: Design and decisions behind reflect's opt-in shared Postgres and pgvector store, so one memory index serves every machine, with the storage adapters, tenancy model, review findings and the tests that pin it.
sidebar:
  order: 11
---

:::note[Design record, backend is current]
Distilled from the working journal and handover on the `freeman/...-reflect-memory` branch of agents-in-a-box (dated 2026-06-18) and checked against this repo: `src/reflect_kb/postgres/`, `src/reflect_kb/cli/graph_engine.py`, `supabase/migrations/`, `tests/postgres/` and the README. The backend is opt-in and off by default. Journal entries that named the author's own Supabase project and credential store are omitted.
:::

## Goal

Use the **same reflect store from several machines**. Not "put the markdown KB in a database": the markdown files stay the local source of truth. Only the per-machine **derived layer** changes.

```text
LOCAL (default)                         SHARED (opt-in)
 notes (.md + sidecars)                  notes (.md + sidecars)
        │                                       │
        ▼                                       ▼
 nano-graphrag                           nano-graphrag  (code unchanged)
  hnswlib + graphml  (per machine)        Postgres storage classes ──▶ pgvector + graph + KV
 QMD index.sqlite (BM25)                 QMD index.sqlite (BM25, still local)
 reflect.db (state)                      reflect.db (state, still local)
```

Enable it by setting **both** `REFLECT_PG_DSN` and `REFLECT_WORKSPACE_ID` and installing the `postgres` extra. The trigger is deliberately `REFLECT_PG_DSN` only, not the generic `DATABASE_URL`, which usually points at an unrelated database. Unset, reflect uses local files exactly as before. Setup is in the repo's [`docs/setup.md`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/docs/setup.md) and the README "Mode 2" section.

## Decisions

| Decision | Why |
|---|---|
| Keep the markdown KB as the source of truth; replace only the vector and graph stores | The files are portable and human-readable; the derived layer is rebuildable |
| No SQLite staging store in the normal path | A second vector store would drift. Embed locally, upsert straight to Postgres |
| Implement **Postgres storage classes for nano-graphrag**, leaving nano-graphrag unchanged | nano-graphrag already has pluggable graph, vector and KV storage (it ships a Neo4j adapter as the reference); only the `*_storage_cls` arguments change |
| Dumb server, smart client | All embedding, entity extraction, Leiden-style clustering and answer synthesis stay on the client. The database stores, scopes and searches. A test statically scans the adapters for any LLM or embedding import |
| `workspace_id` is the hard tenant boundary | First bound parameter of every helper, present in every index and policy |
| Idempotency by content hash plus entity and edge upsert keys | Re-running an insert changes nothing |
| QMD stays local | BM25 is its own SQLite index; the Postgres change touches only the semantic arm |
| No new single-file SQLite backend | "Default file mode is enough"; not built |

## What nano-graphrag persists, and the three adapters

nano-graphrag keeps four pluggable stores: a graph (entities with name, type, description and source ids; relationships with weight and description), **two** vector spaces (entities and chunks) and a key-value store (full documents, text chunks, community reports, LLM cache). reflect supplies:

| Adapter | Base | Notes |
|---|---|---|
| `PgKVStorage` | nano-graphrag KV | tenant-scoped table `ng_kv` |
| `PgVectorStorage` | nano-graphrag vector | pgvector ANN with an HNSW index (`ng_vectors`, 768-d for the default embedder); embeds only via an injected function, never its own model; cosine threshold applied in SQL so results match the local arm |
| `PgGraphStorage` | `NetworkXStorage` | overrides only load (Postgres to networkx) and save (networkx to Postgres), so clustering and community-schema code is reused client-side; no graphml is written |

Code: [`src/reflect_kb/postgres/nanographrag/`](https://github.com/stevengonsalvez/ainb-reflect-memory/tree/main/src/reflect_kb/postgres/nanographrag). Migrations: [`supabase/migrations/0001_reflect_memory_phase1.sql`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/supabase/migrations/0001_reflect_memory_phase1.sql) (memory items, entities, edges, full-text search, RLS) and [`0002_nanographrag_pgvector.sql`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/supabase/migrations/0002_nanographrag_pgvector.sql) (pgvector and the `ng_*` tables). `reflect_kb.postgres` also exposes a typed `MemoryStore` (insert, search, entity lookup by name or alias, graph neighbourhood, evidence packs); that API is separate from the nano-graphrag adapters and recall does not call it.

## Blast radius on recall

Of the retrieval styles on [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/), only the **vector** arm and the **graph** arm read nano-graphrag storage. BM25, rerank, MMR, temporal and the boosts are backend-independent. At 4.1.0 the regression notes counted exactly one of the 57 ports (R1, the graph arm) as storage-coupled. `tests/postgres/test_recall_backend_independence.py` pins that.

## Security review (from the journal)

An automated code review plus a Supabase security review reproduced findings against a throwaway Postgres. Seven findings, all fixed on the branch:

| Severity | Finding | Fix |
|---|---|---|
| High | tenant resolver read a session setting before the signed JWT claim | the JWT is authoritative; the setting is used only when there is no JWT at all |
| Medium | adapter did not set the tenant setting on connect | adapter sets `app.current_workspace` |
| Medium | the `authenticated` role had full CRUD | read-only; writes go through `service_role` |
| Medium | generic `DATABASE_URL` enabled the backend | trigger is `REFLECT_PG_DSN` only |
| Medium | graph save was upsert-only and left stale rows | atomic full replace |
| Low | PUBLIC grants, unpinned `search_path`, unbounded neighbourhood depth, non-finite vectors | revoke PUBLIC, pin `search_path`, clamp depth to 5, guard vectors |

The journal states the merge was gated on a **human** review of the RLS migrations, since an automated pass is not a substitute. This repo's history shows the backend merged on 2026-06-19 (PRs #2 and #3); whether that review took place is not recorded in either place.

## Tests

The journal's final count was 58 tests: 32 that need no database and 26 integration tests against Postgres 17 with pgvector. In this repo they live in `tests/postgres/` (database-free unit tests, plus `nanographrag/` integration tests that auto-skip when no database is reachable). Run the database-free tier with `pytest -m "not integration"`.

What the suite proves:

- storage-contract conformance for the three adapters, and the same contract as the Neo4j reference;
- a graph written by machine A is read by a fresh machine B, idempotently, with no graphml written;
- tenant isolation and RLS fail-closed, including "JWT wins over the session setting";
- **local-versus-Postgres parity**: for `naive`, `local` and `global` queries over a fixed corpus both backends return the same evidence set, with a fake embedder and with the real `all-mpnet-base-v2` model. Tie-break order among equal scores differs between NanoVectorDB and pgvector, so parity is asserted on the evidence set, not the order;
- the markdown KB is byte-identical after a Postgres-backed insert;
- the server stays dumb, both statically and at run time (LLM provider modules poisoned in `sys.modules` still leave the storage path working).

This is storage parity, not a retrieval-quality comparison: nothing here shows the shared store ranks better or worse than local files.

## Known limits (from the journal)

- Real cross-host and concurrent-writer merge behaviour was not exercised; tests use two isolated instances.
- The pipeline tests use a deterministic fake embedder and a canned LLM for most cases (legitimate because both are client-side and swappable); the real-model parity test covers the embedding path.
- Community summaries via the passthrough LLM are placeholder reports, so `global` mode returns structure rather than prose; see [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/#graph-communities).
- A later full golden tier (all 13 lookup types including BM25) is scaffolded in the regression-suite notes; see the [Regression suite](/ainb-reflect-memory/evals/regression-suite/).
