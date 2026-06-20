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

> 📖 **Full documentation → [stevengonsalvez.github.io/agents-in-a-box](https://stevengonsalvez.github.io/agents-in-a-box/)** — architecture, per-harness setup, and the Postgres backend in depth.

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
  <img src="./assets/reflect-topology.svg" alt="reflect component topology — harness hooks feed the reflect engine (capture/index/recall); markdown KB is the source of truth; derived stores run either local (QMD sqlite + nano-graphrag) or shared (Supabase Postgres + pgvector)" width="940" />
</p>

1. **Capture** — `/reflect` analyses your conversation, classifies corrections vs. successes, and writes a Markdown learning note plus a YAML entity sidecar (people, files, libraries, decisions). A `PreCompact` hook fires automatically when the agent compacts a conversation, so nothing is lost.
2. **Index** — notes are dual-indexed: nano-graphrag for semantic + entity-graph search, qmd for fast BM25 lexical search. Both run locally on your machine — nothing leaves it.
3. **Recall** — at every `SessionStart`, a hook runs hybrid search using the new session's working dir + recent commits as the query, fuses the results, reranks by confidence × recency × tag overlap, and injects the top three into the agent's context before you type anything.

---

## Two ways to run: local or shared

The markdown KB (`~/.claude/global-learnings/*.md`) is **always** the local
source of truth, and **all LLM/embedding/clustering always stays client-side**.
What changes between the two modes is only the *derived* vector + graph store.

| | **Mode 1 — Local** (default) | **Mode 2 — Shared** (Postgres) |
|---|---|---|
| Derived store | per-machine: QMD `index.sqlite` (BM25) + nano-graphrag (hnswlib + `.graphml`) | one **Supabase Postgres** (pgvector) for everyone |
| Setup | nothing — works out of the box | set 2 env vars + apply 2 migrations |
| Share across machines | git-sync the markdown KB, then `reflect reindex` on each machine (re-embeds locally) | automatic — every machine queries the same store |
| Best for | solo / single machine / offline | laptop + desktop + CI sharing one memory |

**Mode 1 — local (default).** Nothing to configure. To use the same memory on
another machine, sync the source notes and rebuild the index there:

```bash
# on each machine, after syncing ~/.claude/global-learnings (e.g. via git):
reflect reindex            # re-embeds + rebuilds the local graph/vector store
```

**Mode 2 — shared Postgres.** nano-graphrag runs **unchanged** — it's handed
Postgres-backed storage classes (the same way it ships `Neo4jStorage`), so the
vector + graph + community store lives in one shared DB. Opt in per machine:

```bash
pip install '.[graph,postgres]'                                   # postgres extra = psycopg
psql "$REFLECT_PG_DSN" -f supabase/migrations/0001_reflect_memory_phase1.sql
psql "$REFLECT_PG_DSN" -f supabase/migrations/0002_nanographrag_pgvector.sql
export REFLECT_PG_DSN=postgresql://…        # the trigger (NOT the generic DATABASE_URL)
export REFLECT_WORKSPACE_ID=<uuid>           # hard tenant boundary
```

Unset → Mode 1, unchanged. The DB is **dumb**: no LLM, no embeddings — it
stores, scopes by tenant, and runs ANN/graph reads. Tenant isolation is RLS
(fail-closed) on the direct path + explicit `workspace_id` scoping on the
trusted-worker path; writes need a `service_role` DSN. Full setup + threat
model: [`docs/setup.md`](./docs/setup.md) · [`docs/regression-suite.md`](./docs/regression-suite.md).

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

## Cross-harness — Claude Code · Codex · Copilot

One engine, one knowledge base, three harnesses. A correction captured in Claude
Code is recalled in Codex; a footgun learned in Copilot surfaces back in Claude.
**No extra LLM/embedding configuration on any of them** — recall + indexing run a
local embedding model (no API key), and capture reuses your harness's own LLM
(`claude -p`). You install one thing per harness; the harness does the rest.

### Install per harness

```bash
# Claude Code — native plugin runtime (auto-wires hooks)
claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
claude plugin install reflect@ainb-reflect-memory

# Codex CLI — no plugin runtime, so an adapter copies skills + merges hooks.json
python plugin/adapters/codex/codex_adapter.py install

# GitHub Copilot — adapter writes a native ~/.copilot/hooks/reflect.json
python plugin/adapters/copilot/copilot_adapter.py install
```

### What each harness supports

| Capability | Claude Code | Codex CLI (0.129+) | GitHub Copilot |
|---|:--:|:--:|:--:|
| Plugin runtime | ✅ native | ❌ adapter copies skills | ❌ adapter copies skills |
| Lifecycle hooks (SessionStart / PreCompact / Stop / PostToolUse) | ✅ | ✅ via `~/.codex/hooks.json` | ✅ via `~/.copilot/hooks/reflect.json` |
| **Auto-recall** at session start | ✅ | ✅ | ✅ (`additionalContext`) |
| **Per-prompt** recall surfacing | ✅ `UserPromptSubmit` | ✅ | ⚠️ **manual `/recall`** — Copilot ignores `userPromptSubmitted` output |
| Auto-capture on compact | ✅ | ✅ | ✅ |

### Caveats to know

- **Codex / Copilot capture needs the `claude` CLI.** The reflection drain
  (`reflect-drain-bg.sh`) shells out to `claude -p /reflect <transcript>` to turn
  a transcript into a learning. It's not a *new* key — it reuses your Claude
  auth — but `claude` must be installed. Without it, capture won't drain → run
  `/reflect` manually in-session.
- **Copilot per-prompt recall is manual.** SessionStart auto-recall works, but
  Copilot drops `userPromptSubmitted` hook output, so mid-session recall is `/recall`.
- **Older Codex (< 0.129)** had no hooks — if you're on an old build, install
  with `--no-hooks` and drive `/reflect` + `/recall` manually each session.

All harnesses' memory flows through one ingest pipeline into one store
(`~/.claude/global-learnings/documents/`; `~/.learnings/` is the legacy alias),
dual-indexed into the graph + vector stores (local or shared Postgres).

---

## Components

The same topology as the diagram above, component by component:

| Layer | Component | What it is | Where |
|---|---|---|---|
| Harness | **lifecycle hooks** | fire capture/recall on SessionStart, PreCompact, Stop, PostToolUse | `plugin/` (+ `plugin/adapters/` for Codex/Copilot) |
| Engine | **reflect CLI** (`reflect-kb`) | capture → index → recall orchestrator | `src/reflect_kb/` |
| Source of truth | **markdown KB** | the learning notes — local, always | `~/.claude/global-learnings/*.md` |
| Local store | **QMD** | BM25 lexical index | `~/.cache/qmd/index.sqlite` |
| Local store | **nano-graphrag** | semantic vectors + entity graph | hnswlib + `.graphml` (per machine) |
| Shared store | **Postgres** (opt-in) | pgvector + graph + KV, RLS, tenant-scoped | `src/reflect_kb/postgres/` + `supabase/migrations/` |

**Two version streams — don't confuse them.** The **engine** is the Python
package `reflect-kb` ([`pyproject.toml`](./pyproject.toml)); the **plugin** that
wires it into a harness has its own semver
([`plugin/.claude-plugin/plugin.json`](./plugin/.claude-plugin/plugin.json),
5.0.x). `reflect --version` reports the engine; the manifest reports the wiring.
The engine is the data layer (harness-agnostic); the plugin is the orchestrator.

---

## Documentation

- 🐘 **[docs/setup.md](./docs/setup.md)** — shared Postgres backend: Supabase setup, secret names, migrations, enabling it, threat model
- 🔌 **[plugin/README.md](./plugin/README.md)** — the Claude Code plugin: install flow, hooks, sub-skills, cross-harness adapters, live timeline dashboard
- 📊 **[tests/eval/locomo/REPORT.md](./tests/eval/locomo/REPORT.md)** — full LOCOMO methodology, per-fix ablation, and judge calibration
- 📄 **[LICENSE](./LICENSE)** — MIT

---

## License

MIT. See [LICENSE](./LICENSE).
