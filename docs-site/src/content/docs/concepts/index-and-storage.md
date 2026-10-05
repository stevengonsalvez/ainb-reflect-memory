---
title: Index and storage
description: The KB on disk, how entity sidecars and the lexical and graph indexes are built from it, the shared Postgres mode, the SQLite ledger, and how learnings are superseded or archived.
sidebar:
  order: 4
---

reflect keeps one source of truth and rebuilds everything else from it. The source is markdown notes. The indexes are derived. A separate SQLite ledger tracks provenance, history, and ranking signals. Delete the index and `reflect reindex` restores it.

```
 writers                          source of truth                    derived (rebuildable)
 ┌──────────────────────┐        ┌─────────────────────────┐        ┌──────────────────────────────┐
 │ drain (reflect add)  │──────▶ │ ~/.learnings/documents/ │──────▶ │ nano_graphrag_cache/         │
 │ hooks (direct notes) │        │   <id>.md               │ reindex│   graph + vectors + KV       │
 │ /reflect, /ingest    │        │   <id>.entities.yaml    │        │ qmd collection "learnings"   │
 └──────────┬───────────┘        └─────────────────────────┘        │   (or shared Postgres)       │
            │ provenance, history                                   └──────────────────────────────┘
            ▼
 ~/.reflect/reflect.db   (SQLite ledger, not in git)
```

Field-level detail for notes and sidecars is in [KB format](/ainb-reflect-memory/reference/kb-format/). This page covers how the pieces fit.

## Layout

| Path | Role |
|---|---|
| `~/.learnings/` | KB root. `$GLOBAL_LEARNINGS_PATH` replaces it. `reflect init` makes it a git repo |
| `documents/<id>.md` | One note per learning, frontmatter plus body |
| `documents/<id>.entities.yaml` | Entity sidecar, same stem |
| `nano_graphrag_cache/` | Local graph and vector index. Gitignored by `reflect init` |
| `archived/` | Notes soft-archived from the memory browser |
| `review-queue/` | Pointer files for low-confidence items |
| `shards/<project>/` | Optional per-project (and per-branch) sub-KBs that recall reads first |
| `~/.reflect/reflect.db` | SQLite ledger. Path from `storage.db_path` in `reflect.toml`, or `REFLECT_DB_PATH` |

The KB root is a plain git repo of notes. The index is not committed, so another machine runs `reflect reindex` after a `git pull` and re-embeds locally.

## What writes to `documents/`

| Writer | Path into the KB | Sidecar |
|---|---|---|
| Drain, extract writer | `reflect add --force <note> --entities <sidecar>` | Rendered by `drain_extract.py` |
| Drain, agentic writer | The model writes files, then runs `reflect add` | Written by the model |
| Direct-write hooks | The hook writes `lrn-*.md` straight into `documents/` | None |
| `reflect add FILE` | Copies to `documents/<slug>-<hash6>.md`, then indexes | `--entities FILE`, else heuristic extraction |
| `reflect serve` | Edits and moves existing notes only | Moves with its note |

`reflect add` requires frontmatter with `title`, `category`, and `key_insight`. The file name is the slug of the title plus the first 6 hex of `sha256(title + "\n" + body)`, so re-adding identical content is idempotent. A non-interactive `add` onto an existing file exits 2 unless `--force` is given.

## Building the index

| Command | What it does |
|---|---|
| `reflect add FILE` | Copies the note, loads or generates its sidecar, inserts it into the graph, then runs `qmd update` and `qmd embed` if `qmd` is on `PATH`. A graph failure leaves the note saved and prints a pointer to `reflect reindex` |
| `reflect reindex` | Reads every `documents/*.md` that has frontmatter, generates missing sidecars, and inserts all notes into the graph in one batch. Then syncs qmd |
| `reflect reindex --force` | Deletes the whole `nano_graphrag_cache/` first, then does the above |
| `reflect generate-sidecars [--force]` | Writes heuristic sidecars for notes that lack one, or regenerates all |

Batching matters. nano-graphrag drops community reports and skips persistence when documents are inserted one at a time, so reindex hands it every document in a single call. A malformed sidecar is logged to the `reflect errors` sink and the note is still indexed, with a placeholder entity so nano-graphrag does not abort before persisting the document.

### Two index families

| Index | Answers | Built by | Lives in |
|---|---|---|---|
| Lexical (qmd, collection `learnings`) | Exact terms, file names, error strings (BM25) | `qmd update` then `qmd embed`, run by `add` and `reindex` | qmd's own store. The README places it at `~/.cache/qmd/index.sqlite` |
| Vector and graph (nano-graphrag) | Meaning, and multi-hop "what caused X" | `insert_documents_batch` | `nano_graphrag_cache/` locally, or shared Postgres |

`qmd` is an external CLI. When it is missing, `add` and `reindex` skip the sync and the recall BM25 arm returns nothing; the other arms still run. How recall fuses these is in [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).

### Local graph cache

nano-graphrag runs with its default local storage, which is files under `nano_graphrag_cache/`:

| File | Contents |
|---|---|
| `graph_chunk_entity_relation.graphml` | Entity nodes and relationship edges |
| `vdb_entities.json`, `vdb_chunks.json` | Entity and chunk vectors (nano-vectordb JSON) |
| `kv_store_full_docs.json`, `kv_store_text_chunks.json`, `kv_store_community_reports.json`, `kv_store_llm_response_cache.json` | nano-graphrag's document, chunk, and community stores |

The engine enables nano-graphrag's naive-RAG vector index alongside the graph. `reflect search` modes are `naive` (default, vector only), `local` (entity neighbourhood), and `global` (community reports), and it returns raw context, never a synthesized answer.

:::note
Older docs and the README say the local vectors are hnswlib. The engine sets no custom vector storage, so nano-graphrag's default (nano-vectordb JSON) is used. `recall.py` itself tests `vdb_entities.json` to decide whether a shard is populated. `hnswlib` is installed with the `[graph]` extra but is not referenced by `src/reflect_kb`.
:::

## Entity sidecars

A sidecar is the entity and relationship list for one note. It is what makes the graph arm useful without an LLM at index time: nano-graphrag normally calls a model to extract entities, and reflect swaps in a passthrough "LLM" that returns the sidecar's contents instead.

```yaml
document_id: lrn-pool-timeout-on-cold-start-a1b2c3
extracted_at: "2026-03-04T09:12:00Z"
entities:
  - name: "sqlalchemy"
    type: technology
    description: "Python SQL toolkit"
relationships:
  - source: "lazy pool init"
    target: "queuepool limit error"
    type: caused_by
    description: "Connections open lazily under load"
```

The example is invented. How sidecars flow into the index:

- Each sidecar is converted to nano-graphrag's tuple format (`("entity"<|>name<|>type<|>description)`, `("relationship"<|>source<|>target<|>description<|>strength)`).
- nano-graphrag has no relationship type slot, so the type is prefixed to the description as `[caused_by]`. The graph arm recovers typed edges from the stored graph by that prefix.
- Three sources of sidecars: the drain's extract writer (every entity typed `technology`), an explicit `--entities` file, and the heuristic extractor. The heuristic extractor reads frontmatter, backtick terms, a list of known technology names, and error-like lines, and yields roughly 3 to 8 entities and 2 to 6 relationships. It is rule-based, with no model.
- A note with no sidecar and no extractable entities gets the placeholder entity.

Schema, the relationship type enum, and bitemporal edge fields are in [KB format](/ainb-reflect-memory/reference/kb-format/#entity-sidecar).

## Embeddings

Indexing and recall's diversity step use one embedding model so similarity lives in one space.

| Setting | Default | Notes |
|---|---|---|
| `REFLECT_EMBED_MODEL` | `all-mpnet-base-v2` (768-d) | Any sentence-transformers model. The dimension is read from the model. Changing it needs a fresh `reflect reindex --force`, since vectors are dimension-specific |
| `REFLECT_CE_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker, used at recall time |
| Input cap | 2000 characters per text | Roughly one 512-token window |

Embeds go through the [model daemon](/ainb-reflect-memory/concepts/architecture/#local-model-daemon) when it is available and fall back to loading the model in-process. The `[graph]` extra installs the model stack. Install it with a CPU torch build, as in the [install pages](/ainb-reflect-memory/install/claude-code/).

## Staleness

`reflect search` compares the newest mtime under `documents/` (including the directory itself) against the graph file. If `documents/` is newer, it warns that the index lags. Causes: a manual edit, a failed `add`, or an archive or restore from `reflect serve`. The drain runs `reflect reindex` after any successful entry, so drained learnings are searchable before the next session.

## Shards

Recall prefers `~/.learnings/shards/<project>/` for the current project, and `shards/<project>/branches/<branch>/` for a non-trunk branch or worktree. It uses a shard only if it has notes or a built vector file, otherwise it falls back to the pooled KB. `--global` or `RECALL_GLOBAL=1` pools everything, and `--all-branches` widens a project shard across branches. An explicit `GLOBAL_LEARNINGS_PATH` always wins. The drain and `reflect add` write the pooled KB.

## Shared mode (Postgres)

The same engine can keep its derived store in one Postgres database, so several machines see the same vectors and graph. Notes stay local markdown.

| Piece | Detail |
|---|---|
| Trigger | Both `REFLECT_PG_DSN` and `REFLECT_WORKSPACE_ID` set. The generic `DATABASE_URL` is ignored on purpose |
| Missing deps | With the vars set but `psycopg` absent, the engine raises an error naming the `[postgres]` extra instead of silently going local |
| Schema | Schema `reflect_memory`. Migration `0001` (memory items, entities, edges, search functions, RLS) and `0002` (nano-graphrag storage) in `supabase/migrations/` |
| nano-graphrag tables | `ng_kv` (docs, chunks, community reports, response cache), `ng_graph_nodes`, `ng_graph_edges`, `ng_vectors` |
| Vectors | `vector(768)` with an HNSW cosine index. Rows carry `model` and `dims` so a model mismatch is detectable |
| Division of labour | The database stores, scopes, and runs ANN and graph reads. Embeddings, clustering, and entity extraction all stay client-side |
| Tenancy | `workspace_id` on every table. Row-level security policies match it against `reflect_memory.current_workspace_id()`, and a NULL workspace denies. The trusted-worker path also binds `workspace_id` explicitly in every query |
| Writes | Need a `service_role` or table-owner DSN. Setup and threat model are in `docs/setup.md` in the repo |

nano-graphrag itself is unchanged. The engine passes it three Postgres storage classes (KV, vector, graph) in place of its file-backed defaults. The pinned embedding model means a model change is a new migration plus a re-embed.

## The ledger (reflect.db)

`~/.reflect/reflect.db` is SQLite, created and migrated by `reflect_db.py`. Notes hold only semantic fields and are immutable after write, so everything that changes lives here.

| Group | Tables |
|---|---|
| Learnings | `learnings` (status, scope, `proof_count`, `is_latest`, `supersedes_learning_id`, `superseded_by_learning_id`, `forget_after`, source and session ids), `learning_signals` (importance, maturity, recall, helpful, ignored, and stale counters), `learning_history`, `concept_index` |
| Provenance | `transcripts`, `transcript_chunks`, `chunk_learnings`, `chunk_hashes`, `commit_links`, `artifacts`, `sources` |
| Operations | `events`, `metrics`, `recall_events`, `index_jobs`, `proposals` |
| Higher layers | `observations`, `observation_history`, `conventions_docs`, `project_persona`, `skills`, `slots` |

The drain's `record-chunk` step links each drained transcript to its slice chunks and the learnings written from them, so "what came out of session X" is queryable and re-draining an identical transcript yields no new chunks.

Not every note has a row. Notes from the drain's CREATE path and from `/reflect` workflows do. Notes written only by hooks or by a bare `reflect add` do not, so ledger-side features such as contradiction demotion see only the former.

## Supersession and archival

Learnings leave circulation through several separate mechanisms. Full mechanics, with the exact file and database effects of each, are in [KB format](/ainb-reflect-memory/reference/kb-format/#archive-forget-and-supersession).

| Mechanism | Trigger | Effect |
|---|---|---|
| Belief-revision UPDATE | Drain finds a restated learning | `proof_count` increments, new source appended, history snapshot. The note is not touched |
| Belief-revision DELETE | New evidence contradicts a listed learning | Ledger `status` becomes `reverted`, row kept with the reason |
| Contradiction | A new learning's title negates a similar older one (Jaccard above 0.9, negation marker in exactly one) | Older row gets `is_latest = 0` and `superseded_by_learning_id`, after a history snapshot |
| Revert | A `git revert` is seen by the post-commit hook | That session's learnings get `is_latest = 0` |
| TTL forget | `forget_after` has passed. Hourly `reflect_forget_sweep.py` (a launchd template ships) | Ledger `archived`, and the note and sidecar move into a `.forgotten/` directory |
| Soft archive | Memory browser | Note and sidecar move to `archived/`. Restore moves them back |

Recall enforces retirement. After the arms are fetched and before ranking and final selection, `recall.py` drops a candidate when any of these hold:

- its frontmatter has `superseded_by` set, or `status` is `superseded` or `archived`;
- a note with the same id sits in `archived/` or `documents/.forgotten/`, which covers the graph cache and the temporal arm still returning a note until `reflect reindex`;
- the ledger (`reflect.db`, opened read-only) has a row with `is_latest = 0` or status `superseded` or `archived` for it.

:::note
Ledger rows carry their own generated ids, so recall links a row to a note through the note at the row's `artifact_path` (its file stem or frontmatter id) or a matching `content_hash`. The TTL sweep and anything else that records `artifact_path` link reliably. A row with neither (the cascade's CREATE writes no `artifact_path`) cannot be matched to a note file, so a contradiction demotion on such a row stays ledger-only until the note is archived or carries `superseded_by`. With no ledger file, recall skips that signal and nothing fails. Set `REFLECT_RECALL_INCLUDE_SUPERSEDED=1` to bypass the filter when debugging.
:::

## Team routing

`src/reflect_kb/write_flow.py` implements confidence-gated routing for a team KB repo: high confidence commits to `main`, medium opens a draft PR on `knowledge/<slug>`, low (or no team KB configured) writes a pointer to `review-queue/`. It is tested library code. No `reflect` CLI command calls it today, and the `reflect team` and `reflect share` commands that older docs describe do not exist in the 0.3.0 CLI.
