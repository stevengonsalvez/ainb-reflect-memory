---
title: Retrieval roadmap
description: The April 2026 "retrieval gap" diagnosis and six-phase plan for getting prior learnings back into sessions, what each phase became in the shipped code, and which open questions were settled.
sidebar:
  order: 12
---

:::note[Historical plan, checked against current code]
Source: the 2026-04-20 handover and the commit messages of `89faa5356` (v3.1.0) and `e80992567` on the `reflect/retrieval-phase-1-2` branch of agents-in-a-box. The plan itself lived in GitHub issue #40 of that repo; this page uses only what the handover and commits quote. The "Today" column was verified against `plugin/` in this repo.
:::

## The gap

After the v3.0 plugin rework the capture side was complete: 222 learnings indexed with entity sidecars, in GraphRAG and QMD. The handover's diagnosis was that the KB was **write-only**. Prior knowledge reached a session by only three routes: an explicit `/research`, a manual `learnings search` in a shell, or the user remembering. "That's an archive, not a knowledge base."

## The six phases

| Phase | Planned deliverable | Today |
|---|---|---|
| 1 | `/reflect:recall` skill and `recall.py`, hybrid QMD plus GraphRAG, rerank | **Shipped** in v3.1.0 (2026-04-23). Subcommands such as `related`, `project`, `graph <entity>`, `recent`, `stale` were not built; the skill exposes `--mode naive/local/global` instead |
| 2 | SessionStart priming hook that injects the top-N relevant learnings (highest leverage) | **Shipped**: `session_start_recall.py`. Query is project name plus branch plus recent commit tokens; at most 3 notes and 1500 characters. Registered by default, not opt-in |
| 3 | Skills (`/research`, `/plan`, `/critique`, `/commit`, `/implement`) query the KB automatically | **Shipped in April** as a recall preamble injected into tier-1 and tier-2 toolkit skills (commit `0b94fa98a`); those skills live outside this repo |
| 4 | PostToolUse passive suggestions on error and signal patterns, rate limited | **Reshaped.** No hook matching the spec as written. Nearest: a PreToolUse policy and context lookup (`pretooluse_context.py`), SubagentStart recall (`subagent_start_recall.py`), and a UserPromptSubmit recall (`user_prompt_submit_recall.py`). PostToolUse is used for capture (mini-learnings), not suggestions |
| 5 | Temporal filters, commit linkage (`fixes lrn-xxx`), stale detection | **Largely shipped** in 4.1.0: query date parsing and a temporal arm (R5, R6), commit-ref verification (M5) and a `commit_links` table, a post-commit capture hook (SG2), per-skill staleness (R14) |
| 6 | Close-the-loop: helpfulness tracking reranks future results | **Not built.** v3.1.0 started an append-only recall log "for Phase 6"; today there is a `recall_events` table with a `feedback` column, `helpful_count` and `ignored_count` columns on learnings, and the A4 follow-up-rate diagnostic. Nothing in `recall.py` reads them to rerank |

## How the first design changed on contact

The first foundation spec (handover section 5) fused the two engines with a weighted blend: `qmd_score * 0.6 + graph_degree * 0.4`. What shipped instead:

```text
recall.py ─┬─ reflect search --mode naive  (GraphRAG vector)   ┐
           └─ qmd search -c learnings      (BM25)              ┤ parallel
                                                                ▼
                         RRF fuse, k=60, dedup by id or chunk hash
                                                                ▼
                  confidence x recency x tag rerank ──▶ filter ──▶ limit ──▶ format
```

- **RRF instead of a weighted blend**, so scores from different arms never need calibrating against each other.
- **`qmd search` (BM25), not `qmd query`.** `qmd query` expands and reranks with an LLM and took about 25 s; `qmd search` takes about 0.45 s. The BM25 arm is there for exact tokens that embeddings miss (error messages, type names).
- Same day, `learnings add` was fixed to run `qmd update` before `qmd embed`: before the fusion commit, every write populated QMD and nothing ever queried it.
- A 1 h per-query cache, a no-op exit 0 when the KB is absent, and the append-only recall log were in the first cut.

Later phases of the recall stack (graph arm, cross-encoder, MMR, gates, sharding) are on [Retrieval styles](/ainb-reflect-memory/concepts/retrieval-styles/).

## Open questions from the handover, and what happened

| Question | Outcome |
|---|---|
| Should SessionStart retrieval be opt-in or opt-out? Proposed opt-in until tuned | Shipped on by default in the plugin hooks; the hook has its own tighter limits (3 notes, 1500 characters, overlap gate 0.2) |
| Relevance threshold for passive suggestions? Start at 0.8 | Passive suggestions were not built as specced; 0.8 reappears as the score threshold of the SessionStart short-circuit probe (R11), with a 30-day freshness window |
| Expose retrieval as an MCP tool for non-Claude agents? Check with the user first | Not built. Non-Claude harnesses get the same hooks through the Codex, Copilot and Hermes adapters |
| Where to inject pre-retrieved context in `/plan`: before or after scaffolding? | Settled in the toolkit skills outside this repo; not recorded here |

## Why keep this page

It records the motive for the whole recall stack: capture without retrieval is an archive. It also shows the one phase that is still open, closing the loop with usage feedback. The schema to support it (`recall_events`, helpful and ignored counters) exists, so Phase 6 is a ranking change rather than a data-model change.

See also [How reflect evolved](/ainb-reflect-memory/design/history/) and the [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).
