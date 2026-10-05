---
title: Problem and fit
description: Why a bigger CLAUDE.md or AGENTS.md hits a ceiling, what reflect does about it, and where it does and does not fit.
sidebar:
  order: 3
---

reflect captures what you teach a coding agent (corrections, decisions, the reasons behind non-obvious choices) and recalls the right piece at the right moment of a later session, across machines and harnesses.

![Timeline of one coding session: recall injects at SessionStart and each prompt, signals and capture write out at tool calls, Stop and PreCompact, and the index closes the loop to the next session](/ainb-reflect-memory/diagrams/reflect-session-timeline.svg)

## The problem is context engineering, not file size

Every harness gives you one persistent memory primitive: a static instructions file you maintain by hand (`CLAUDE.md`, `AGENTS.md`, `MEMORY.md`, `copilot-instructions.md`), loaded into the context window at the start of every session.

That has a ceiling. The window is finite and shared with the actual task. Every line loaded "just in case" is bloat whenever the current session does not need it, and it crowds out the files, diffs and reasoning the task does need.

```
context window = [ task: files, diffs, plan, tool output ] + [ memory ]
                                                                   ^
                          front-loaded "just in case": dead weight
                          when irrelevant, and capped by window size
```

A bigger file is the wrong axis. The questions that matter:

| Situation | Needed behavior |
|---|---|
| A correction from three weeks ago ("regenerate clients when the shared proto changes") | Reaches the agent only in the session about to touch that proto |
| The reason behind a non-obvious choice (the double `recalcTax` call fixes an EU VAT rounding bug) | Recalled when someone questions that code, invisible otherwise |
| A changed decision (moved from JWT to server-side sessions in June) | Overrides the stale fact instead of sitting in a file nobody re-reads |

The goal is not to store more. It is to store the signal-bearing moments and query the right one at the right time, spending context only when it pays. That is a retrieval problem.

## What decides fit

Agent-memory tools differ on four axes that determine whether they suit a coding workflow:

1. **Selective or everything.** Whole-session capture is useful but accumulates noise and unbounded data.
2. **Separate model key.** Your coding agent already has a model and subscription. Many memory tools want a second provider key just for memory.
3. **Infrastructure.** An always-on server (Postgres, Redis, a daemon, a vector DB) is operational weight and often cloud-bound.
4. **Coding signals.** Corrections, test outcomes, git events: captured as structured events, or left for an LLM to find in prose.

The scored tool-by-tool view is on the [comparison](/ainb-reflect-memory/start/comparison/) page.

## Where reflect fits

Design line: the brain runs client-side, the store is dumb, signals are typed.

```
harness hooks ──▶ capture ──▶ markdown KB (source of truth) ──▶ index (QMD + nano-graphrag)
      ▲             │ typed signals                                      │
      │             │ (corrections, tests, git, todos, ...)              │
      └──────────── recall ◀───────────────────────────────────────────┘
```

| reflect's approach | Problem it addresses |
|---|---|
| **Selective capture.** Only transcripts with a signal are queued; the drain slices them to the signal windows. Near-duplicate learnings are held for a merge-or-keep check (cosine threshold 0.97). Contradictions are detected at write time. Learnings can carry a `forget_after` TTL. | Whole-session noise and unbounded growth |
| **No separate key.** Embeddings and reranking run locally. The drain calls `claude -p` with your existing Claude auth. | A second provider key and subscription for memory |
| **Local files.** Markdown notes plus a local nano-graphrag store, with an optional BM25 index. Shared Postgres only if you want one memory across machines. | A mandatory server or cloud dependency |
| **Typed signals.** Hooks route git commits, test pass/fail, tool loops, todo completions, permission replies, contradictions and idle sweeps as structured events. Revised learnings can trigger a skill refresh. | Leaving the important events to probabilistic extraction |
| **Cross-harness.** Claude Code, Codex and Copilot read and write one KB; Hermes is wired in through shims. | Single-harness memory |
| **Reviewable source of truth.** Notes are markdown with clean diffs; volatile bookkeeping lives in a local SQLite database (`~/.reflect/reflect.db`). | Opaque vector blobs |

On the no-extra-key axis alone, other tools tie reflect (claude-mem also reuses Claude auth). The case rests on the combination: selective, no extra key, local, typed signals, cross-harness, MIT licensed.

## Limits to know before adopting

- **Capture needs `claude`.** The background drain shells out to the `claude` CLI on every harness. "No extra key" means no second key, not no model spend: drain runs use your Claude quota (the plugin 5.2.5 changelog measured about $0.40 for one extraction of a 1.5 MB transcript). Track it with `/reflect:cost`.
- **Retrieval is not best in class.** reflect owns a large recall engine (`recall.py` is about 3,500 lines) built from ideas ported from other systems. Benchmark results are a small pilot; see [benchmarks](/ainb-reflect-memory/evals/benchmarks/).
- **Recall can return nothing by design.** The out-of-domain gate suppresses weak matches. A quiet session start is normal.
- **BM25 needs `qmd`.** It is an optional external binary, not installed by the `[graph]` extra. Without it, hybrid recall degrades to the graph and vector arm.
- **Some scheduled maintenance is macOS-first.** The idle, drain, forget, maintenance and synthesis timers ship as launchd plists.
- **Local by default.** Sharing across machines means git-syncing the notes and running `reflect reindex` on each, or running the Postgres backend.

## Next

1. [Comparison](/ainb-reflect-memory/start/comparison/): the other tools, scored.
2. [Overview](/ainb-reflect-memory/start/overview/): the capture, drain, index, recall loop.
3. [Quickstart](/ainb-reflect-memory/start/quickstart/): try it.
