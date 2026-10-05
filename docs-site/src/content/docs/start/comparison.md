---
title: Comparison
description: reflect against Hindsight, Mem0, ByteRover, claude-mem, agentmemory, Honcho and OpenViking on four axes, with a scored matrix, a benchmark pilot and an honest build-versus-adopt verdict.
sidebar:
  order: 4
---

Eight coding-agent memory systems on four axes. Short version: tools that need no second API key tend to hoard noise; tools that curate well make you run a server and pay a second provider; and none of the others capture corrections, tests, git events or skill changes as typed signals.

:::caution
Statements about reflect are checked against this repository. Statements about other projects are a snapshot from reflect's original evaluation (mid-2026), taken from their public docs and code. They age quickly. Pricing is deliberately left out. Check each project before deciding.
:::

For the underlying problem, see [problem and fit](/ainb-reflect-memory/start/problem-and-fit/).

## The four axes

| # | Axis | Question |
|---|---|---|
| 1 | Selective or everything | Does it store only signal-bearing moments, or every tool call? |
| 2 | Separate key | Does memory need its own LLM or embedding provider and subscription? |
| 3 | Infrastructure | Does it need an always-on server (Postgres, Redis, a daemon, a vector DB)? |
| 4 | Coding signals | Are corrections, test outcomes, git events and skill upgrades captured as typed events, or hoped for from prose? |

## The landscape

| Tool | What it stores | Separate LLM or embed key | Runs as | Coding signals first-class | License |
|---|---|---|---|---|---|
| **reflect** | Selective learnings; markdown is the source of truth | No separate key; the drain calls `claude -p`, embeddings are local | Local files; optional shared Postgres | Yes: corrections, tests, tool loops, git, todos, permissions, contradictions, skill refresh | MIT |
| [Hindsight](https://github.com/vectorize-io/hindsight) | LLM-extracted facts and mental models | Yes for writes (see note) | Local daemon, Docker (FastAPI and Postgres), or cloud | No: extracted from prose | MIT |
| [Mem0](https://github.com/mem0ai/mem0) | LLM-extracted facts | Yes: OpenAI by default at ingest | Library, or Docker (Postgres and Neo4j) | No: hooks, but no correction, git or test capture | Apache-2.0 and SaaS |
| [ByteRover](https://github.com/campfirein/byterover-cli) | Curated markdown tree | Curation makes its own LLM calls | Local files and a node daemon; optional cloud sync | No: curation is agent-directed, not passive | Elastic 2.0 (not OSI open source) |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Every tool call, compressed to observations | No: reuses Claude auth and local embeddings | Always-on Bun daemon; optional ChromaDB | No: probabilistic extraction | Apache-2.0 |
| [agentmemory](https://github.com/rohitg00/agentmemory) | Every tool call, verbatim | Optional; value degrades without it | Always-on Rust daemon | No: raw events, little structure without an LLM | Apache-2.0 |
| [Honcho](https://github.com/plastic-labs/honcho) | User and peer models | Yes when self-hosted; cloud is metered | Postgres, Redis and a deriver worker; or cloud | No: built for end-user personalization | AGPL-3.0 |
| [OpenViking](https://github.com/volcengine/OpenViking) | Tiered LLM-extracted memories and skills | Yes: OpenAI or Volcengine by default | Rust, Go and C++ server, always on | No: git, test and skill capture not first-class | AGPL-3.0 |

Note on Hindsight: its `retain` step needs an LLM. At evaluation time its "reuse your Claude subscription" loopback was documented as personal-use only under Anthropic's terms, so it could not be a shipped default for a tool other people install.

Pattern: the only other tool that matches reflect on "no extra key" (claude-mem) stores everything and grows noisy. The tools that curate well (Hindsight, OpenViking, ByteRover) lose on key or infrastructure. Typed signal capture is a column of "no".

## Scored matrix

Scores are 1 to 5, assigned by reflect's author and weighted for one workflow: capturing corrections into behavior, cheaply, with no infrastructure. Treat the weights as an opinion, not a measurement.

| Criterion (weight) | reflect | Hindsight | claude-mem | ByteRover | Mem0 | agentmemory | Honcho | OpenViking |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Signal capture (0.25) | 5 | 2 | 2 | 2 | 2 | 2 | 1 | 2 |
| No extra key (0.22) | 5 | 2 | 5 | 3 | 2 | 3 | 2 | 2 |
| Local-first (0.20) | 5 | 3 | 3 | 4 | 2 | 2 | 2 | 2 |
| Selective, low noise (0.16) | 4 | 4 | 2 | 4 | 3 | 1 | 2 | 4 |
| Retrieval quality (0.12) | 4 | 5 | 3 | 3 | 4 | 3 | 4 | 4 |
| License and portability (0.05) | 5 | 5 | 4 | 2 | 4 | 4 | 2 | 2 |
| **Weighted total** | **4.72** | 3.03 | 3.08 | 3.06 | 2.50 | 2.28 | 1.99 | 2.56 |

:::note[Sensitivity]
These weights favor reflect's strengths. Re-weight toward retrieval quality and ecosystem maturity and Hindsight wins: it is MIT licensed, production-grade and has far more integrations. If your priority is best recall with the least code you maintain, adopting Hindsight as a retrieval backend is the smarter call.
:::

## Token economics

Memory looks cheap if you only price retrieval. The real cost is the write side (LLM extraction and consolidation) plus the read side (context injection).

| System | Write side | Read side | Extra spend |
|---|---|---|---|
| reflect | Gated and queued; one `claude -p` extraction per queued transcript (Sonnet by default), local embeddings | Local vector, graph and optional BM25; no LLM unless HyDE is on | No second key; drain runs consume your Claude quota (see `/reflect:cost`) |
| claude-mem | Haiku on your Claude auth, per tool-call batch | Local FTS and ONNX vectors | No second key, but high write volume |
| Hindsight | LLM extraction on `retain` | Vector and graph; synthesis has a cost | Separate key or metered cloud |
| Mem0 | OpenAI extraction at ingest | Vector and BM25 | Separate OpenAI key, plus a paid tier for graph memory |
| OpenViking | VLM extraction plus tier summaries | Vector, recursive | Separate provider key |
| Honcho | Deriver LLM per batch | Context reads are cheap; dialectic queries are metered | One to three keys self-hosted, or per-query |
| agentmemory | Optional LLM compression, off by default | Local embeddings and rank fusion | None if the LLM is off, but value degrades |
| ByteRover | Curation uses its own LLM calls | BM25 with LLM fallback | Own key or a paid plan |

:::caution[Dangerous default]
Auto-retain every turn combined with a large auto-recall is how memory tools quietly burn a subscription. reflect's capture is gated and queued (not every turn), and the drain runs under hard caps (turn cap, wall-clock timeout, a 2M-token poison threshold, daily entry cap). Recall at session start is out-of-domain gated and capped at 1500 characters; a token budget is available through `REFLECT_RECALL_MAX_TOKENS` but off by default.
:::

## Benchmark pilot

reflect 4.1.0 on [LOCOMO](https://github.com/snap-research/locomo), a long-term conversational memory benchmark. This is a **small pilot**: 50 stratified questions (10 per category) from one conversation (`conv-26`), answered and written by Sonnet, graded by an Opus reference judge. Retrieval runs reflect's real engine; the dialogue-to-note extraction is a LOCOMO-specific adapter. Configurations were kept or dropped by their score on these same 50 questions, and per-cell noise is about 0.1.

| Config (Opus judge) | single-hop | multi-hop | temporal | open-domain | adversarial | overall |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| reflect 4.1.0, tuned recall and extraction | 0.70 | 0.75 | 0.80 | 0.50 | 0.90 | 0.73 |
| plus retrieval fixes | 0.80 | 0.80 | 0.80 | 0.70 | 0.90 | 0.80 |

The fixes are two env-gated, no-new-key options, both off by default: a stronger local embedder (`REFLECT_EMBED_MODEL=BAAI/bge-base-en-v1.5`) and HyDE query expansion (`REFLECT_RECALL_HYDE=1`, which uses reflect's own `claude -p`).

![LOCOMO positioning of reflect against other memory systems](/ainb-reflect-memory/img/start-locomo-positioning.png)

reflect lands mid-field, on par with Memobase and Zep and above Mem0. Newer systems (ByteRover, Honcho, Hindsight) score higher but are self-reported on their own harnesses. Judges and harnesses differ across the field, so read this as directional placement, not a ranking. Methodology, ablation and judge calibration: [benchmarks](/ainb-reflect-memory/evals/benchmarks/).

## Observability and correction

When memory is wrong, can you see it, edit it, delete it and trace its origin?

- **reflect:** learnings are plain markdown you open and edit in your editor, or curate through the [memory browser](/ainb-reflect-memory/guides/memory-browser/). Notes carry provenance such as the source session. Volatile bookkeeping lives in a local SQLite database, keeping git diffs clean. Optional per-row TTL (`forget_after`) and contradiction detection retire stale beliefs.
- **Most others:** facts live in Postgres or vector stores, inspected through an API or SQL. claude-mem ships a web viewer, but observations are database rows. ByteRover is the exception, with an editable markdown tree.

## How a correction becomes behavior

In most systems a correction is a sentence in a transcript that an extraction LLM may or may not turn into a fact. reflect routes it as typed signals at hook time: contradiction, git commit and revert, test pass and fail, tool loop, todo completion, permission reply, idle sweep, and knowledge gaps from empty recalls. When a revision changes a learning that a skill depends on, it queues an automatic skill refresh.

The aim is to capture what changed, not only what was said, and feed it back as the next session's behavior. Hook-level detail: [hooks reference](/ainb-reflect-memory/reference/hooks/) and [capture](/ainb-reflect-memory/concepts/capture/).

## Recommendation and decision rule

:::note[Decision rule]
Adopt an external memory provider only if it (a) needs no second API key or subscription, (b) runs without a mandatory always-on server, and (c) captures coding signals as typed events. In the original evaluation no single external tool cleared all three. So: build the thin, differentiated layer (capture, typed signals, local-first) and port, rather than reinvent, the commodity retrieval, keeping it behind a seam so a stronger backend can be swapped in.
:::

**Verdict: build and port, with eyes open.** reflect ported retrieval ideas from Hindsight and others (graph expansion, RRF fusion, cross-encoder rerank, temporal handling). The cost is owning that code: retrieval is the least differentiated and highest-maintenance part of reflect, and it trails the best systems on benchmarks.

## If you would adopt instead: four pilot tests

1. **Zero extra credentials.** Install on a fresh machine with only the agent's existing auth. Do capture and recall work with no new key or account? (reflect needs the `claude` CLI for capture; claude-mem passes; most others fail.)
2. **No always-on server.** Reboot. Does memory work with nothing running in the background? (reflect passes; daemon-based tools fail.)
3. **Correction to behavior.** Correct the agent once. Next session, does the rule resurface without manual curation?
4. **Noise over 30 days.** Run daily for a month. Is recall still sharp, or flooded with stale and duplicate entries? (Fire-hose tools degrade here.)

## Next

1. [Quickstart](/ainb-reflect-memory/start/quickstart/): run reflect on your machine.
2. [OKF versus reflect](/ainb-reflect-memory/design/okf-vs-reflect/): design note comparing Google's Open Knowledge Format (OKF) with reflect.
3. [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/): what reflect does at retrieval time.
