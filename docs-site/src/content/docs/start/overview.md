---
title: Overview
description: What plugly-reflect-memory is, the capture, drain, index, recall loop on one screen, and who it is for.
sidebar:
  order: 1
---

plugly-reflect-memory is long-term memory for AI coding agents. It captures corrections and decisions from your sessions as markdown learnings, indexes them locally, and injects the relevant ones into later sessions before the agent acts. Correct a mistake once; the next session starts knowing it.

It ships as two pieces: the **`plugly-reflect-memory` CLI** (Python package `plugly-reflect-memory`, the engine) and a **plugin** (hooks and skills that wire the engine into a harness).

## The loop

```
 session ──▶ capture ──▶ drain ──▶ index ──▶ recall ──▶ next session
   ▲           hooks      claude -p   QMD +     hooks          │
   │           queue      writes      nano-     inject         │
   │           (no LLM)   markdown    graphrag  top results    │
   └────────────────────────────────────────────────────────────┘
```

![Timeline of one coding session showing where recall injects learnings and where capture writes them](/plugly-reflect-memory/diagrams/reflect-session-timeline.svg)

| Stage | What runs | When | Output |
|---|---|---|---|
| **Capture** | Lifecycle hooks gate the transcript with a $0 regex check and append worthwhile ones to a queue. Hooks never call an LLM. | `Stop`, `SessionEnd`, `PreCompact`, `SubagentStop`, plus signal armers on tool calls | `~/.reflect/pending_reflections.jsonl` |
| **Drain** | A detached script slices each queued transcript to its signal windows and runs one `claude -p` extraction (Sonnet by default) under hard caps. | Background, launched from `SessionStart`, debounced | Markdown learning plus `.entities.yaml` sidecar in `~/.learnings/documents/` |
| **Index** | `plugly-reflect-memory add` and `plugly-reflect-memory reindex` build the nano-graphrag store (local embeddings plus entity graph). If the optional `qmd` binary is on `PATH`, it also maintains a BM25 index. | After each drain, or manually | `~/.learnings/nano_graphrag_cache/`, `~/.cache/qmd/index.sqlite` |
| **Recall** | Query from cwd, branch and recent commits (session start) or the prompt text. Fuse arms, rerank, apply an out-of-domain gate, inject. | `SessionStart`, `UserPromptSubmit`, `SubagentStart`, on demand via `/plugly-reflect-memory:recall` | Hooks inject up to 3 learnings (1500 chars) as `additionalContext`; `/plugly-reflect-memory:recall` returns up to 10 |

Details: [capture](/plugly-reflect-memory/concepts/capture/), [drain](/plugly-reflect-memory/concepts/drain/), [index and storage](/plugly-reflect-memory/concepts/index-and-storage/), [recall pipeline](/plugly-reflect-memory/concepts/recall-pipeline/). End-to-end picture: [architecture](/plugly-reflect-memory/concepts/architecture/).

## Design points

- **Markdown is the source of truth.** Learnings are plain files you can read, edit, diff and delete. The indexes are derived and rebuildable with `plugly-reflect-memory reindex`.
- **Local by default.** Embeddings run locally (`all-mpnet-base-v2`), and no separate embedding or LLM API key is needed. The only model call is the drain's `claude -p`, which uses your existing Claude auth and counts against your Claude usage (inspect with `/plugly-reflect-memory:cost`).
- **Selective.** Capture is gated and queued, not every turn. Recall injects nothing when nothing relevant matches.
- **Typed signals.** Hooks route git commits, test outcomes, tool loops, todo completions, permission replies and contradictions as structured events, not just prose for an LLM to find.
- **One KB, several harnesses.** A learning captured in one harness is recalled in the others.

## Harnesses

| Harness | Install path | Page |
|---|---|---|
| Claude Code | Native plugin (marketplace) | [Claude Code](/plugly-reflect-memory/install/claude-code/) |
| Codex CLI | Adapter script merges skills and hooks | [Codex](/plugly-reflect-memory/install/codex/) |
| GitHub Copilot CLI | Adapter script writes native hooks file | [Copilot](/plugly-reflect-memory/install/copilot/) |
| Hermes (fleet-lambda) | Adapter deploys skills and shims | [Hermes](/plugly-reflect-memory/install/hermes/) |
| Codex, Copilot, Cursor (extra channel) | APM package generated from `plugin/`; agent wiring only | [APM](/plugly-reflect-memory/install/apm/) |

:::note
The drain shells out to the `claude` CLI on every harness. Codex and Copilot users still need `claude` installed for background capture to run.
:::

## Two version streams

| Component | Where | Current |
|---|---|---|
| Engine (`plugly-reflect-memory` CLI) | `pyproject.toml`, `plugly-reflect-memory --version` | 0.3.0 |
| Plugin (hooks, skills, adapters) | `plugin/.claude-plugin/plugin.json` | 5.2.5 |

## Who it is for

Good fit:

- You use a coding agent daily and keep repeating the same corrections (tooling choices, migration order, library footguns).
- You work across repos, machines or harnesses and want one memory behind all of them.
- You want memory you can inspect and edit, with no extra service or key to run.

Weaker fit:

- You need an end-user personalization layer for a product. plugly-reflect-memory targets coding workflows.
- You want a hosted, multi-user service out of the box. Shared mode needs your own Postgres (see [index and storage](/plugly-reflect-memory/concepts/index-and-storage/)).
- A short hand-written instructions file already covers everything you need.

## Next

1. [Quickstart](/plugly-reflect-memory/start/quickstart/): zero to a recalled learning.
2. [Problem and fit](/plugly-reflect-memory/start/problem-and-fit/): why a bigger instructions file is the wrong axis.
3. [Comparison](/plugly-reflect-memory/start/comparison/): where plugly-reflect-memory sits among other agent-memory tools.
