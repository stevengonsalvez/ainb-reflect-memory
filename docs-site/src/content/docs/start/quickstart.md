---
title: Quickstart
description: Shortest path from nothing installed to a learning recalled in a new session, with every command checked against the code.
sidebar:
  order: 2
---

Two tracks. Steps 1 to 4 prove the engine works on its own (no harness needed). Steps 5 and 6 wire it into Claude Code so recall and capture happen automatically. Other harnesses: see [Codex](/ainb-reflect-memory/install/codex/), [Copilot](/ainb-reflect-memory/install/copilot/), [Hermes](/ainb-reflect-memory/install/hermes/).

## Prerequisites

| Need | Why |
|---|---|
| Python 3.11+ | `requires-python = ">=3.11"` |
| [`uv`](https://docs.astral.sh/uv/) | Installs the CLI, and every plugin hook runs as `uv run --script` |
| `claude` CLI | The background drain calls `claude -p` to write learnings (steps 5 and 6 only) |
| `qmd` (optional) | Adds a BM25 arm to recall. Without it, recall uses the graph and vector arm only |

## 1. Install the engine

```bash
uv tool install --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'
reflect --version
```

`reflect --version` prints `reflect, version 0.3.0` (the engine version, not the plugin's). `--torch-backend cpu` skips roughly 4 GB of CUDA wheels; drop it on a GPU box. If your `uv` rejects the flag, use `UV_TORCH_BACKEND=cpu uv tool install ...` instead.

## 2. Create the knowledge base

```bash
reflect init
```

Creates `~/.learnings/` with `documents/` and `nano_graphrag_cache/`, runs `git init`, and writes a `.gitignore` that excludes the cache. Set `GLOBAL_LEARNINGS_PATH` first to use another location.

## 3. Add a learning

A learning is a markdown file with YAML frontmatter. `title`, `category` and `key_insight` are required; `confidence` and `tags` are optional but used for ranking.

```bash
cat > bun-not-node.md <<'EOF'
---
title: "Use Bun, not Node, in the billing-api repo"
category: tooling
key_insight: "billing-api runs on Bun; node commands fail on the lockfile and test runner."
confidence: high
tags: [bun, node, billing-api]
---

## Problem
The agent ran `npm install` and `node --test` in billing-api and corrupted the lockfile.

## Fix
Use `bun install` and `bun test`. The repo has a bun.lockb and no package-lock.json.
EOF

reflect add ./bun-not-node.md
```

`reflect add` copies the note to `~/.learnings/documents/<slug>-<hash>.md`, auto-generates an entity sidecar (heuristic, no LLM), and inserts it into the graph index. The first run downloads the embedding model, so allow a few minutes. If `qmd` is installed, `add` also runs a `qmd` sync, which can take up to two minutes on large KBs.

Expected tail of the output:

```
Indexed into graph
Added: /home/you/.learnings/documents/use-bun-not-node-in-the-billingapi-repo-a44a62.md
Title: Use Bun, not Node, in the billing-api repo
Category: tooling
Entities: 8, Relationships: 6
```

## 4. Recall it

```bash
reflect search "which package manager for billing-api"
```

The result panel contains the note, found by meaning rather than exact words. Useful flags: `--mode naive|local|global` (default `naive`), `--limit`, `--format rich|json|simple`. See the [CLI reference](/ainb-reflect-memory/reference/cli/).

This is the raw engine query. What a harness injects is the fuller recall pipeline (fusion, rerank, gating): [recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).

## 5. Install the plugin (Claude Code)

```bash
claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
claude plugin install reflect@ainb-reflect-memory
```

Start a new Claude Code session and run:

```
/reflect:recall bun billing-api
```

Output is a markdown block headed `Prior learnings relevant to ...` with one line per hit, showing your note. From now on, the `SessionStart` and `UserPromptSubmit` hooks run the same recall automatically and inject up to 3 learnings (capped at 1500 characters) before the agent acts.

:::note
Auto-injection is gated. If the best hit shares too few terms with the query (default minimum overlap 0.2, env `REFLECT_RECALL_MIN_OVERLAP`), nothing is injected. A session opened in a repo called `billing-api` overlaps this note; an unrelated repo will not. If a recall comes back empty, check the [troubleshooting guide](/ainb-reflect-memory/guides/troubleshooting/).
:::

## 6. Capture a real learning

Correct the agent in a session ("no, we use bun here"), then end the session or let it compact. What happens next:

1. `Stop`, `SessionEnd` or `PreCompact` runs a $0 gate over the transcript and, if it has signal, appends it to `~/.reflect/pending_reflections.jsonl`. No LLM is called.
2. The next `SessionStart` launches the drain in the background (debounced to once per 10 minutes). It slices the transcript and runs `claude -p` on Sonnet to extract learnings.
3. Written learnings are indexed automatically (the drain runs `reflect reindex` after a successful batch).

To capture immediately instead of waiting, run `/reflect` inside the session.

Check it worked:

```bash
reflect stats          # document, entity and relationship counts
tail ~/.reflect/drain.log
```

In Claude Code, `/reflect:status` shows pending reviews, sidecar coverage and GraphRAG health, and `/reflect:cost` shows what the drain has spent.

## Where things live

| Path | Contents |
|---|---|
| `~/.learnings/documents/` | Learning notes and `.entities.yaml` sidecars (source of truth) |
| `~/.learnings/nano_graphrag_cache/` | Local vector and graph index (derived) |
| `~/.cache/qmd/index.sqlite` | BM25 index, only if `qmd` is installed (derived) |
| `~/.reflect/` | Queue (`pending_reflections.jsonl`), `drain.log`, `errors.json`, config overrides |

## Next

1. [Overview](/ainb-reflect-memory/start/overview/): how the loop fits together.
2. [Per-harness install](/ainb-reflect-memory/install/claude-code/): hooks, adapters and options in full.
3. [Memory browser](/ainb-reflect-memory/guides/memory-browser/): `reflect serve` to browse and curate what was captured.
