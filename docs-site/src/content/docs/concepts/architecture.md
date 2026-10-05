---
title: Architecture
description: How reflect is put together. Hooks capture, a detached drain writes learnings, a local engine indexes them, and recall injects the best ones into the next session.
sidebar:
  order: 1
---

reflect is a loop with four stages and one rule: hooks never call a model. Hooks record cheap facts and queue transcripts. A detached drain turns the queue into learnings. The engine indexes them. Recall injects them at the start of the next session or prompt.

![reflect component topology: harness hooks feed the engine (capture, index, recall); the markdown KB is the source of truth; derived stores run local (qmd and nano-graphrag files) or shared (Postgres with pgvector).](/ainb-reflect-memory/diagrams/concepts-topology.svg)

## The loop

```
 harness session
 ┌────────────────────┐
 │ hooks (no LLM)     │  write notes directly       ┌───────────────────────┐
 │  PostToolUse       │ ──────────────────────────▶ │ ~/.learnings/         │
 │  UserPromptSubmit  │  (test fix, todo done,      │   documents/*.md      │◀─ source of truth
 │  Notification      │   mini-learning, perm.)     └──────────┬────────────┘
 │                    │                                        │ reflect add / reindex
 │  PreCompact, Stop, │  enqueue transcript path    ┌──────────▼────────────┐
 │  SessionEnd, ...   │ ─────────┐                  │ derived index         │
 └─────────▲──────────┘          │                  │  qmd (BM25)           │
           │ additionalContext   ▼                  │  nano-graphrag files  │
           │             ┌──────────────┐           │  or shared Postgres   │
           │             │ ~/.reflect/  │           └──────────┬────────────┘
           │             │ pending_     │ SessionStart         │
           │             │ reflections  │ ─▶ detached drain    │
           │             │ .jsonl       │    gate, slice,      │
           │             └──────────────┘    one claude -p ────┘ writes notes
           │                                                    + sidecars, reindexes
           └────────────────── recall (hybrid search, rerank, budget) ◀──────┘
```

| Stage | Page | One line |
|---|---|---|
| Capture | [Capture](/ainb-reflect-memory/concepts/capture/) | Hooks write deterministic notes and queue transcripts. $0, no model. |
| Drain | [Drain](/ainb-reflect-memory/concepts/drain/) | A detached script gates, slices, and runs one model call per transcript. Capped on turns, time, tokens, and daily count. |
| Index | [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/) | `reflect add` and `reflect reindex` build the lexical and graph indexes from the markdown. |
| Recall | [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/) | Fan out to the arms, fuse, rerank, gate, pack to a budget, inject. |

## Components

| Component | Where | Runs | Calls a model | Owns |
|---|---|---|---|---|
| Lifecycle hooks | `plugin/hooks/*.py`, `plugin/skills/recall/hooks/*.py` | In the harness process, per event | No | Queue, armed watchers, hook-written notes under `~/.reflect/` and the KB |
| Drain | `plugin/hooks/reflect-drain-bg.sh`, `plugin/scripts/reflect_cascade.py`, `plugin/scripts/drain_extract.py` | Detached, started by `SessionStart` | Yes, `claude -p`, one call per transcript | Lock, debounce, retry and cost ledgers, poison file |
| Engine | `src/reflect_kb/` (`reflect` CLI) | On demand | No LLM. Local embedding and rerank models | Index build and search, `reflect serve`, the model daemon |
| Recall | `plugin/skills/recall/scripts/recall.py` | Called by recall hooks and `/reflect:recall` | No | Recall cache and log under `~/.reflect/` |
| Ledger | `plugin/scripts/reflect_db.py` | Imported by scripts | No | `~/.reflect/reflect.db` (SQLite) |

Design rules the code enforces:

- **Hooks are silent-fail.** Every hook wraps its body, writes a breadcrumb on error, and exits 0. A broken hook cannot break the session.
- **Producers and the consumer are separate.** `PreCompact`, `Stop`, `SessionEnd`, and `SubagentStop` only append to the queue. The detached `SessionStart` drain is the only thing that runs `/reflect` from hooks.
- **No extra API key.** Capture shells out to the `claude` CLI. Embeddings and reranking run on a local model (`all-mpnet-base-v2` and a MiniLM cross-encoder by default).
- **Markdown is the source of truth.** Indexes and the SQLite ledger are derived or auxiliary. A lost index is rebuilt with `reflect reindex`.

:::note
The drain always shells out to `claude`, whichever harness started the session. Codex and Copilot sessions wire the same `reflect-drain-bg.sh` at session start, so `claude` must be on `PATH` (or set `REFLECT_DRAIN_CLAUDE_BIN`).
:::

## One session, end to end

![Timeline of one coding session: SessionStart and UserPromptSubmit read prior knowledge into context, PostToolUse and Stop write signals, PreCompact flushes before compaction, and the index closes the loop to the next session.](/ainb-reflect-memory/diagrams/reflect-session-timeline.svg)

## Harness coverage

Same scripts, three wiring files. Event names differ in case between harnesses.

| Harness | Wiring file | Events wired |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` | 13: `SessionStart`, `UserPromptSubmit`, `Notification`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `PostCompact`, `SubagentStart`, `SubagentStop`, `SessionEnd`, `PreCompact` |
| Codex CLI (0.129+) | `plugin/codex-hooks.json` | 10: as Claude minus `Notification`, `PostToolUseFailure`, `SessionEnd` |
| GitHub Copilot | `plugin/copilot-hooks.json` | 13: camelCase equivalents (`sessionStart`, `userPromptSubmitted`, `agentStop` for `Stop`, and so on), no `postCompact`, plus `errorOccurred` |

Per-event behaviour is in [Hooks reference](/ainb-reflect-memory/reference/hooks/).

## Two storage modes

The markdown notes are always local. Only the derived vector and graph store moves.

| | Local (default) | Shared (Postgres) |
|---|---|---|
| Derived store | `nano_graphrag_cache/` under the KB root, plus qmd's own index | Four tables in the `reflect_memory` schema (`ng_kv`, `ng_graph_nodes`, `ng_graph_edges`, `ng_vectors`) |
| Enable | Nothing | Install the `[postgres]` extra, apply `supabase/migrations/0001_*.sql` and `0002_*.sql`, set `REFLECT_PG_DSN` and `REFLECT_WORKSPACE_ID` |
| Across machines | Sync the notes (git), run `reflect reindex` on each machine | Every machine reads the same store |

Both env vars must be set for the Postgres store to switch on. `REFLECT_PG_DSN` is the trigger, not the generic `DATABASE_URL`. nano-graphrag itself is unchanged; it is handed Postgres storage classes. The database does no LLM or embedding work, and tenancy is the `workspace_id` column enforced by row-level security. Details in [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/#shared-mode-postgres).

## Local model daemon

Each `reflect search`, `embed`, or `rerank` used to cold-boot torch (about 3.5 GB RSS). Since 5.2.0 a unix-socket daemon loads the models once and serves every CLI call. It auto-spawns on first use, exits after `REFLECT_IDLE_TIMEOUT` seconds idle (default 1800), and one daemon serves every KB for a given user, model pair, and `TMPDIR`. If it cannot start, calls fall back to in-process loading, with a lock that allows one concurrent load. `REFLECT_NO_DAEMON=1` disables it.

## Where state lives

| Path | Purpose | Detail |
|---|---|---|
| `~/.learnings/` (or `$GLOBAL_LEARNINGS_PATH`) | KB root: notes, sidecars, derived graph cache | [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/) |
| `~/.reflect/` (or `$REFLECT_STATE_DIR`) | Queue, armed watchers, drain ledgers, logs, `reflect.db` | [Capture](/ainb-reflect-memory/concepts/capture/), [Drain](/ainb-reflect-memory/concepts/drain/) |
| `$REFLECT_STATE_DIR/reflect.toml` or `~/.reflect/reflect.toml` | User config over the bundled `plugin/reflect.toml` | [Configuration](/ainb-reflect-memory/reference/configuration/) |
