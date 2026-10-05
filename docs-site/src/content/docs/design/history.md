---
title: How reflect evolved
description: Timeline of reflect from a single /reflect skill in January 2026 through the GraphRAG KB, the recall layer, the cost rearchitecture, extraction into its own repo and the 5.x releases, with the evidence for each step.
sidebar:
  order: 2
---

:::note[Historical]
This page is a timeline, not a description of the current system. Everything marked with a date is taken from git history (commit dates, tags) or from the journals and handovers named in each row. For how reflect works today, start at the [Architecture](/ainb-reflect-memory/concepts/architecture/) page.
:::

The short version:

```text
Jan 2026   /reflect skill (v1 monolith)
Feb 2026   global learnings KB: nano-graphrag + QMD, two engines
Apr 2026   v3 plugin (SQLite, providers) ──▶ v3.1 recall + SessionStart ──▶ BM25 + RRF
May 2026   reflect-kb imported into the monorepo ──▶ Codex adapter, hooks
May 31     4.0.0 cost rearchitecture (after a 41.5M-token incident)
Jun 2026   4.1.0 recall upgrade ──▶ LOCOMO pilot ──▶ shared Postgres backend (opt-in)
Jun 20     5.0.0 standalone repo, tagged releases
Jul-Aug    5.1 to 5.2.5: model daemon, memory browser, fleet, extract writer
```

## Timeline

Sources: `git log` in `stevengonsalvez/agents-in-a-box` (the original monorepo) and in this repo (770 commits at the time of writing, including the imported history), `plugin/CHANGELOG.md`, the 2026-04-20 handover, and the shared-Postgres `JOURNAL.md`.

| Date | What happened | Evidence |
|---|---|---|
| 2026-01-25 | `/reflect` becomes a portable skill with a consolidate command (later archived as the "v1 monolith"). | commit `7759db85f` |
| 2026-02-12 | Distributed knowledge capture added to the toolkit. | `29e3e6ace` |
| 2026-02-16 | **Global learnings GraphRAG system.** A cross-project KB indexed by nano-graphrag (entity graph) and QMD (BM25 plus vectors), run as complementary engines. | `245970620`; March knowledge-system doc |
| 2026-03-09 | File-lock concurrency guard for GraphRAG indexing. | `f37bf0281` |
| 2026-03-13 to 17 | First write-up of the "four memory tiers" design (context memory, project-local notes, global KB, instincts) and why both engines run: QMD answers "what matches", GraphRAG answers "what is connected". | `846986615`, `84458c310` |
| 2026-04-05 | Project-memory archival (`--ingest-memories`). | `02fe3533a` |
| 2026-04-14 | **v3: reflect reworked as a plugin** with colon-namespaced sub-skills, SQLite state, layered TOML config and multi-tool providers (Claude, Codex, Copilot, Gemini). | `7dcb213b0`, `10c904b31` |
| 2026-04-20 | v1 monolith archived. Handover: v3.0.0 merged, 222 learnings indexed, capture side complete, KB described as "write-only". Issue #40 opens the retrieval roadmap. | `7bc64bbf3`; handover |
| 2026-04-23 | **v3.1.0: `/reflect:recall` plus SessionStart auto-retrieval.** Same day: BM25 (`qmd search`) fused with the vector arm by RRF. Two design records written (v3.2 single PR, v4 universal install). | `89faa5356`, `e80992567`, design records |
| 2026-05-17 | `reflect-kb` (the Python engine and CLI) imported into the monorepo; the plugin moves to a root `plugins/reflect/`. | `79a4645`, `e8addfa` |
| 2026-05-20 to 22 | Codex adapter with SessionStart and PreCompact hooks (PR #149), silent-fail hook helpers (3.5.0, PR #150), UserPromptSubmit recall plus PostToolUse mini-learning plus Stop enqueue (PR #154). | merge commits |
| 2026-05-31 | **4.0.0 cost rearchitecture** after one background drain used 41.5M tokens in 9.6 minutes for zero new learnings. | CHANGELOG 4.0.0; [Cost rearchitecture](/ainb-reflect-memory/design/cost-rearchitecture/) |
| 2026-06-04 | One-step install flow (PR #217). | `e93955c` |
| 2026-06-10 | Recall eval harness with hermetic KB and golden queries; the recall-upgrade commits (R1 to R16, M, A, S, SG series) begin landing. | `4e0a3b1` and following |
| 2026-06-17 | **4.1.0 "57 ports"** recall upgrade (PR #248). | CHANGELOG 4.1.0 |
| 2026-06-18 | **LOCOMO harness** added; `toolkit/` deleted from the monorepo. Same day the shared-Postgres work is journaled. | `0c6ce6c`, `33f9e6b`, `JOURNAL.md` |
| 2026-06-19 | LOCOMO env knobs (embedder swap, HyDE, recall budget) and README results; the **Postgres backend lands in this repo** (PRs #2 and #3); plugin marketplace added. | `234ad4e`, `db0a60a`, `d934a66` |
| 2026-06-20 | **5.0.0: standalone repo.** reflect moves to `stevengonsalvez/ainb-reflect-memory` with tagged, pinned releases and an automated release workflow. No runtime behaviour change. | CHANGELOG 5.0.0 |
| 2026-06-21 to 28 | Restructure fixups, hook registration, skills namespacing, drain watchdog (5.0.1 to 5.0.4). | CHANGELOG |
| 2026-07-01 | Transcripts-to-issues pipeline (`reflect issues`). | `322c645` |
| 2026-07-06 to 15 | Memory browser (`reflect serve`) with curation UI. CPU-torch install guidance (5.1.1). | `9ccc0cc`, `15b9001` |
| 2026-07-10 | **5.2.0: persistent model daemon** (warm embed and rerank about 0.2 s vs 7.5 s cold). | CHANGELOG 5.2.0 |
| 2026-07-14 | Fleet ingest with quarantine, domain and authority boosts, Hermes adapter. | `32caf21`, `62b9fd1` |
| 2026-07-15 to 18 | Drain outage fixes (5.2.1: skills never registered; 5.2.2: skill paths and turn budget) and the opt-in single-shot extract writer (5.2.3). | CHANGELOG |
| 2026-08-11 | 5.2.4 (oversized transcripts no longer silence the drain) and 5.2.5 (extract writer becomes the default). | CHANGELOG |
| 2026-10-05 | Starlight docs site scaffolded in `docs-site/`. | `477cdba` |

## What changed in the retrieval design

| Stage | Retrieval shape | Source |
|---|---|---|
| Feb to Mar 2026 | Two independent engines, QMD (BM25 plus vectors plus LLM rerank) and GraphRAG (naive, local, global), used by `/research` and a manual `learnings search` | March knowledge-system doc |
| Apr 20 | KB described as "write-only": nothing flows back into a session without the user asking | handover |
| Apr 23 | `recall.py`: vector arm and BM25 arm in parallel, RRF (k=60), then confidence x recency x tag rerank; SessionStart injects at most 3 notes / 1500 chars | commits `89faa5356`, `e80992567` |
| Jun 10 to 17 | Graph arm, temporal arm, cross-encoder, MMR, bounded boosts, gates, caches, sharding, staged and tiered inject | 4.1.0 |
| Jun 18 to 19 | Measured on LOCOMO; HyDE and embedder swap added as opt-in knobs | LOCOMO REPORT |
| Jul 10 | Models held in a daemon so parallel recalls share one process | 5.2.0 |

The style-by-style view is on [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/); the original six-phase plan and what became of each phase is on [Retrieval roadmap](/ainb-reflect-memory/design/retrieval-roadmap/).

## What changed in capture

| Stage | Capture shape | Source |
|---|---|---|
| v1/v2 | One `/reflect` skill run in the live session | v1 monolith |
| v3 (Apr) | Plugin with sub-skills, SQLite state, providers, PreCompact queue | handover |
| v3.5 (May) | Hooks wired for Stop, UserPromptSubmit and PostToolUse; a SessionStart "surfacer" and a background drainer both consume the queue | commits, cost plan |
| 4.0 (May 31) | Gate and slice before any model, one consumer, hard caps, Sonnet by default, cost log | [Cost rearchitecture](/ainb-reflect-memory/design/cost-rearchitecture/) |
| 5.2.3 to 5.2.5 | One tool-free model call returns a JSON action list that a script executes; the agentic loop is a fallback | CHANGELOG |

## Notes on the shared-Postgres line

The Postgres and pgvector work is the **late** line, not the origin. nano-graphrag with local files was the engine from February 2026. The Postgres work (JOURNAL dated 2026-06-18, PR #302 in the monorepo, re-landed here on 2026-06-19) replaces only the per-machine derived layer (vectors, graph, community reports) with a shared store behind an opt-in switch, and keeps the markdown KB as the source of truth. It is current code, not abandoned: see [Shared Postgres backend](/ainb-reflect-memory/design/shared-postgres-backend/).

## Where the numbers in the older docs no longer hold

- The 2026-04-20 handover counts 222 learnings, 347 graph nodes and 10 communities: a snapshot of the author's KB on that day, not a property of the system.
- The v3-era docs place notes in `~/.learnings/documents/learnings/`. Current `reflect add` writes flat to `documents/`.
- The CHANGELOG 4.1.0 entry says the new arms are gated by knobs that "default off or to the pre-4.1 behavior" and, in the same paragraph, that they are "live out of the box". In the code the retrieval arms (R1, R2, R3, R5, R6, R9) default **on**; see [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/).
- The "57" is the count of proof files at 4.1.0. The directory holds 60 proof files today.
