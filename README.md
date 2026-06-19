# reflect

> **Long-term memory for AI coding agents — correct once, never again.**

<p align="center">
  <img src="./assets/reflect-mascot.png" alt="reflect mascot — an elephant that never forgets" width="280" />
</p>

<p align="center">
  <a href="https://github.com/stevengonsalvez/ainb-reflect-memory/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/stevengonsalvez/ainb-reflect-memory/ci.yml?branch=main&label=CI" alt="CI status" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/python-%3E%3D3.11-blue.svg" alt="Python >=3.11" /></a>
  <a href="./tests/eval/locomo/REPORT.md"><img src="https://img.shields.io/badge/LOCOMO%20J-0.80-olive" alt="LOCOMO J 0.80" /></a>
</p>

reflect captures every correction and design decision your AI assistant makes, indexes them into a hybrid **GraphRAG + BM25** knowledge base, and **auto-recalls** the relevant ones at the start of every new session — automatically, before the first token of your prompt is generated.

Works across **Claude Code**, **Codex CLI**, and **GitHub Copilot** — same engine, same KB, three harnesses.

---

## Why

If you've used AI coding assistants for more than a week, you've corrected the same mistake twice. Maybe ten times. The assistant doesn't remember that:

- Your team uses Bun, not Node, for that one repo
- The Postgres migration in your project must run before the seed
- That third-party library has a footgun you discovered last month
- "When I ask you to delete files, also clean the imports"

The context window forgets the moment the session ends. reflect fixes that by **capturing** corrections as structured learnings, **indexing** them into a searchable knowledge base, and **recalling** the relevant ones at the start of every new session — so a fix you make once is a fix you never have to make again.

---

## Install

The engine lives at the repo root — install it with `uv` and the `[graph]` extra (pulls the full GraphRAG + vector stack):

```bash
uv tool install --upgrade 'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'
```

Verify with `reflect --version`.

### Quickstart

```bash
reflect init                                    # one-time: create the KB at ~/.claude/global-learnings/
reflect add ./my-solution.md                    # capture a learning (optional --entities sidecar)
reflect search "how did we fix the tokio panic" # hybrid GraphRAG + BM25 recall
```

### Plugin (Claude Code)

The **plugin** (hooks + skills) that wires reflect into your agent harness lives under [`plugin/`](./plugin/). Install it from this repo's marketplace:

```bash
claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
claude plugin install reflect@ainb-reflect-memory
```

See [plugin/README.md](./plugin/README.md) for the lifecycle hooks, sub-skills, and the Codex / Copilot adapters. (`ainb reflect bootstrap` installs the engine + prints system-tool steps in one shot.)

---

## How it works

reflect runs a **capture → index → recall** loop:

<p align="center">
  <img src="./assets/reflect-knowledge-system.svg" alt="reflect architecture — agent session feeds the capture→index→recall engine (GraphRAG + QMD) backed by three memory tiers" width="900" />
</p>

1. **Capture** — `/reflect` analyses your conversation, classifies corrections vs. successes, and writes a Markdown learning note plus a YAML entity sidecar (people, files, libraries, decisions). A `PreCompact` hook fires automatically when the agent compacts a conversation, so nothing is lost.
2. **Index** — notes are dual-indexed: nano-graphrag for semantic + entity-graph search, qmd for fast BM25 lexical search. Both run locally on your machine — nothing leaves it.
3. **Recall** — at every `SessionStart`, a hook runs hybrid search using the new session's working dir + recent commits as the query, fuses the results, reranks by confidence × recency × tag overlap, and injects the top three into the agent's context before you type anything.

---

## Shared memory across machines (Postgres backend)

By default reflect's derived stores (vectors + entity graph + community reports)
are **local files** — each machine rebuilds its own. The optional
`reflect_kb.postgres` backend moves those into one shared **Supabase Postgres**,
so laptop, desktop, and CI all query the *same* memory. The markdown KB stays
the local source of truth; **all LLM/embedding/clustering stays client-side** —
the database only stores, scopes by tenant, and runs ANN/graph reads.

```
 machine A ─┐  (file KB local · QMD/sqlite lexical local · client keeps the brain)
 machine B ─┤── nano-graphrag (unchanged) ──▶ Supabase Postgres
 machine C ─┘     storage_classes() swap        ng_vectors (pgvector) · ng_graph_* · ng_kv
```

nano-graphrag runs **unchanged** — it's handed Postgres-backed storage classes
(the same way it ships `Neo4jStorage`). Opt in per machine:

```bash
pip install '.[graph,postgres]'           # postgres extra = psycopg
psql "$REFLECT_PG_DSN" -f supabase/migrations/0001_reflect_memory_phase1.sql
psql "$REFLECT_PG_DSN" -f supabase/migrations/0002_nanographrag_pgvector.sql
export REFLECT_PG_DSN=postgresql://…       # trigger; NOT the generic DATABASE_URL
export REFLECT_WORKSPACE_ID=<uuid>          # hard tenant boundary
```

Unset → original local-file behavior, unchanged. Tenant isolation is enforced by
RLS (fail-closed) on the direct path and explicit `workspace_id` scoping on the
trusted-worker path; writes require a `service_role` DSN. Details +
threat-model: [`docs/setup.md`](./docs/setup.md) ·
[`docs/regression-suite.md`](./docs/regression-suite.md).

---

## Benchmark

reflect 4.1.0 evaluated on [LOCOMO](https://github.com/snap-research/locomo) (long-term conversational memory). **Preliminary**: a category-stratified pilot graded by an **Opus** reference LLM-judge. Retrieval runs reflect's **real** engine; the dialogue→note extraction is a documented LOCOMO-domain adapter. The judge is load-bearing — cheaper judges systematically under-credit valid paraphrases — so every figure uses the Opus reference.

| config · Opus judge | single-hop | multi-hop | temporal | open-domain | adversarial | **overall** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **reflect 4.1.0 + retrieval fixes** | 0.80 | 0.80 | 0.80 | 0.70 | 0.90 | **0.80** |

The retrieval fixes are two additive, env-gated, **zero-new-API-key** knobs: a stronger local embedder (`REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5`) and **HyDE** query-expansion (`REFLECT_RECALL_HYDE=1`, reusing reflect's own `claude -p`). Both default off — shipped behavior is unchanged.

![LOCOMO positioning — reflect vs other memory systems](tests/eval/locomo/results/locomo_positioning.png)

reflect lands mid-field — on par with Memobase / Zep, above Mem0 — while the newest systems (ByteRover, Honcho, Hindsight) sit higher but are self-reported on their own harnesses. Judges and harnesses differ across the field, so treat this as **directional placement, not a strict ranking**. Full methodology, per-fix ablation, and judge calibration: [`tests/eval/locomo/REPORT.md`](./tests/eval/locomo/REPORT.md).

---

## Cross-harness

One engine, one knowledge base, three harnesses. A correction captured in Claude Code is recalled in Codex; a footgun learned in Copilot surfaces back in Claude.

| Harness | Wiring | Memory source ingested |
|---|---|---|
| **Claude Code** | Native plugin — SessionStart / UserPromptSubmit / Stop / PreCompact hooks | `~/.claude/projects/<hash>/memory/*.md` |
| **Codex CLI** | Python adapter (`plugin/adapters/codex/`) | `~/.codex/memories/*.md` + `~/.codex/AGENTS.md` |
| **GitHub Copilot** | Python adapter (`plugin/adapters/copilot/`) | `~/.copilot/AGENTS.md` |

All sources flow through one ingest pipeline and land in one place: `~/.claude/global-learnings/documents/` (the engine default; `~/.learnings/` is the legacy alias of the same store), dual-indexed into graph + vector stores.

---

## Repo layout

```
ainb-reflect-memory/
├── pyproject.toml          # the reflect engine (Python package `reflect-kb`)
├── src/reflect_kb/         # CLI + retrieval engine (GraphRAG + BM25)
│   └── postgres/           # optional shared-Postgres backend (MemoryStore + nano-graphrag adapters)
├── supabase/migrations/    # Postgres schema: memory/entities/edges + ng_* (pgvector) + RLS
├── tests/                  # engine tests + the LOCOMO benchmark harness
│   ├── postgres/           # Postgres backend tests (no-DB + integration, auto-skip)
│   └── eval/locomo/        # REPORT.md, positioning plot, eval scripts
├── docs/                   # engine docs (usage, architecture)
├── schemas/                # learning-note + entity-sidecar schemas
├── scripts/                # helper scripts
├── assets/                 # mascot + branding
└── plugin/                 # the Claude Code plugin (hooks + skills)
    ├── .claude-plugin/plugin.json   # plugin manifest (v4.1.x)
    ├── skills/             # reflect, reflect:recall, reflect:ingest, …
    ├── hooks/              # SessionStart / PreCompact / Stop / PostToolUse
    └── adapters/           # codex + copilot cross-harness adapters
```

**Two version streams — don't confuse them.** The **engine** is the Python package `reflect-kb`, versioned in [`pyproject.toml`](./pyproject.toml). The **plugin** that wires the engine into the agent harness follows its own semver in [`plugin/.claude-plugin/plugin.json`](./plugin/.claude-plugin/plugin.json) (currently 4.1.x). When asked "what version of reflect is installed?" you usually want both: `reflect --version` for the engine and the plugin manifest for the harness wiring.

**Key split:** the engine is the data layer — it knows nothing about any specific harness. The plugin is the orchestrator — it knows when to capture, drain, recall, and surface.

---

## Documentation

- 🐘 **[docs/setup.md](./docs/setup.md)** — shared Postgres backend: Supabase setup, secret names, migrations, enabling it, threat model
- 🔌 **[plugin/README.md](./plugin/README.md)** — the Claude Code plugin: install flow, hooks, sub-skills, cross-harness adapters, live timeline dashboard
- 📊 **[tests/eval/locomo/REPORT.md](./tests/eval/locomo/REPORT.md)** — full LOCOMO methodology, per-fix ablation, and judge calibration
- 📄 **[LICENSE](./LICENSE)** — MIT

---

## License

MIT. See [LICENSE](./LICENSE).
