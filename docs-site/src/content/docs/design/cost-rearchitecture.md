---
title: Cost rearchitecture (4.0)
description: Why reflect's background drain was rebuilt in May 2026 after a 41.5M-token incident, the five workstreams and locked decisions, what shipped, and how the drain behaves today.
sidebar:
  order: 10
---

:::note[Design record, partly superseded]
Written from the 2026-05-31 plan and interview spec (`plugin/docs/cost-rearchitecture-plan.md`, `cost-rearchitecture-plan-spec.md`) and checked against the code at `main` (`48968dd`, 5.2.5). Where the live drain differs from the plan, the table in [What is live now](#what-is-live-now) wins.
:::

## The incident

A background drain started `claude -p "/reflect <transcript>"` on one finished session and used **41.5M tokens in 9.6 minutes** for **zero net-new learnings**. The cost was context times turns times cache misses, not model price.

| Symptom | Measured | Cause |
|---|---|---|
| 41.5M tokens, 9.6 min | 223 Opus turns at about 176K context each | unbounded agentic loop on a fat context |
| Whole transcript in context | a 493 KB transcript, about 123K tokens | the skill ingested the full transcript, no slicing |
| Cache not helping | `cache_read` frozen at 21,670 while `cache_creation` grew 59K to 199K | only the static head cached; volatile content above the transcript busted the rest (hypothesis in the plan) |
| One transcript reflected 16 times | 16 distinct sessions that day | no enqueue dedup, no processed set |
| Daily cap 20 exceeded to 61 | race in the count check | non-atomic rate limit |
| 223 turns despite `--max-turns 25` | run ended `end_turn` just under the 600 s timeout | only wall-clock bounded it |
| Zero new learnings | the transcript was a reflect run itself | no skip-gate for reflect-on-reflect or no-signal sessions |

A 30-day backfill showed about 1.2B tokens across 446 reflect runs (roughly $7k estimated), almost all on Opus. Switching Opus to Sonnet alone is about 5x; bounding context, turns and cache misses is 20 to 50x. The plan did both.

## Topology before and after

```text
BEFORE
 precompact hook ─┐  append, no dedup        ┌─▶ SessionStart "surfacer"  (injects into live session)
 stop hook ───────┴─▶ pending_reflections ───┤
                      .jsonl                  └─▶ background drainer ──▶ claude -p /reflect (Opus, Bash)

AFTER
 precompact / stop ─▶ gate + dedup ─▶ queue ─▶ ONE drainer ─▶ cascade ─▶ write ─▶ cost log
   (no model)         $0 skips        (JSONL)  (flock, debounce)  gate, slice, extract
```

## Five workstreams

| | Workstream | What it does |
|---|---|---|
| W1 | Circuit breaker | turn cap, wall-clock cap, post-hoc token poison, atomic lock, debounce, kill switch, model pin |
| W2 | Skip-gate and dedup | regex gate over the dialogue at enqueue: skip reflect-on-reflect, no-signal and clean sessions, anything already queued or processed |
| W3 | Observability | full token envelope per run, a cost reporter, a backfill of history |
| W4 | Cascade | gate, then slice the transcript to the signal-bearing windows (about 10x smaller), then a bounded model call |
| W5 | Structural rebuild | retire the surfacer, self-healing graphml repair, neutral working directory, weekly synthesis, launchd timers |

## Decisions locked in the interview

| # | Decision | Choice |
|---|---|---|
| 1 | Queue consumer | keep the background drainer, retire the SessionStart surfacer |
| 2 | Scheduler | launchd timer (about 10 min) plus a lock |
| 3 | Cascade roll-out | straight to default, no A/B harness (telemetry is the safety net) |
| 4 | Backfill window | 30 days |
| 5 | Gate aggressiveness | reflect on any signal including LOW; skip only no-signal, clean-success and reflect-on-reflect |
| 6 | Existing backlog | re-gate it |
| 7 | Dedup | content-hash fast path, then vector similarity above 0.85 |
| 8 | Caps | 8 turns, 180 s, poison above 2M tokens |

Out of scope by design: rewriting the GraphRAG engine, changing the recall path, a cost dashboard web app.

## What shipped (4.0.0, 2026-05-31)

- **Measured on real data:** a backlog re-gate dry-run collapsed 114 queued entries to 13 (81 reflect-on-reflect, 20 duplicates, so 89 percent was worthless); the incident transcript now skips at the gate for $0.
- **Deferred:** the full SQLite queue migration. Path dedup plus a signal hash gave idempotency in practice, so the JSONL queue stayed.
- **Retired:** the SessionStart surfacer (`sessionstart_drain_reflections.py`) became a no-op; the drainer is the only consumer.

## What is live now

Checked against `plugin/hooks/reflect-drain-bg.sh`, `plugin/scripts/*.py`, `plugin/launchd/` and `plugin/CHANGELOG.md`.

| Item | 4.0.0 plan | Live (5.2.5) |
|---|---|---|
| Turn cap `REFLECT_DRAIN_MAX_TURNS` | 8 | **16** (5.2.2: 8 turns counted assistant messages, below the minimum honest workflow of about 7, so every run hit the cap and wrote nothing) |
| Wall-clock `REFLECT_DRAIN_TIMEOUT` | 180 s | **300 s** |
| Token poison `REFLECT_DRAIN_TOKEN_MAX` | 2,000,000 | 2,000,000 |
| Daily cap `REFLECT_DRAIN_DAILY_MAX` | 20, atomic | 20; sums the `entries` field so $0 skips never consume it |
| Debounce `REFLECT_DRAIN_DEBOUNCE_SEC` | 600 | 600 |
| Model `REFLECT_DRAIN_MODEL` | `sonnet` | `sonnet` |
| Cascade switch | `REFLECT_CASCADE=1` | `REFLECT_DRAIN_CASCADE`, default `1` |
| Gate floor `REFLECT_GATE_MIN_SIGNAL` | `any` | not present in code; the gate always reflects on any signal |
| Writer | agentic `claude -p /reflect` | `REFLECT_DRAIN_WRITER`, default **`extract`** (5.2.5): one tool-free call returns a JSON action list that `drain_extract.py` executes; `agentic` is the fallback when no slice exists |
| Input bound | not in plan | `REFLECT_DRAIN_MAX_INPUT_CHARS` default 60000 (5.2.4); oversized transcripts are quarantined with `entries=0`, not charged to the daily cap |
| Queue | SQLite queue | still the JSONL queue; deferred |
| Cost reporter | `reflect cost` CLI | `plugin/scripts/reflect_cost.py` and the `/reflect:cost` skill (`--since`, and `--by` one of day, transcript, model, outcome, writer); it is not a `reflect` binary subcommand |
| Backfill and backlog re-gate | `backfill_costs.py`, `regate_backlog.py` | moved to `plugin/scripts/archive/` as completed one-shot migrations |
| Weekly synthesis | Opus batch | `reflect_synthesis.py` plus `com.reflect.synthesis.plist`; also an auto-trigger after 30 new learnings (C2) |
| Launchd templates | drain, synthesis | drain, synthesis, forget, idle, maintenance |

### Two later corrections to the same drain

Both are outages, kept here because they show what the cost design did not catch.

- **5.2.1.** The drain captured nothing for 11 days: 5.1.0 had dropped the `skills` array from `plugin.json`, so every `claude -p "/reflect ..."` returned `Unknown command` with exit 0 and zero turns. The drain scored a zero-turn run as `ok`, which removes the entry from the queue, so each no-op run discarded a transcript. A zero-turn run is now `fail_unknown_command` and aborts as an install fault.
- **5.2.4.** From 2026-07-31 to 2026-08-10 no learning was written while logs and the statusline looked healthy. Transcripts of about 1.5 MB (162K tokens of dialogue against a 200K context with about 64K already spent) got `Prompt is too long`; the rejection was classified `poisoned` and charged to the daily cap, so 20 oversized transcripts used the day's budget and starved everything else. Fixes: oversized input is quarantined for free and the drain bounds input to 60000 characters head plus tail.
- **5.2.3 and 5.2.5.** On two 1.5 MB transcripts, the agentic writer took 17 turns and 1,503,423 tokens ($1.07) and kept nothing; the extract writer took 1 turn and 77,982 tokens ($0.40) and wrote 3 learnings. The agentic loop re-sends its growing conversation each turn, so cost grows roughly quadratically (measured earlier: 20 turns is 6.8M tokens, $4.42), and bounding the input cannot fix growth that comes from the loop itself.

## Lessons the record supports

- The dominant cost lever is context times turns times cache-miss, not model price.
- A cap that counts failures against a shared budget turns one bad input class into a total outage. Zero-token rejections should cost nothing.
- "Exit 0 with zero turns" must not be scored as success.
- Silent health: the logs said `daily cap reached`, which looks exactly like normal throttling.

See also [Capture](/ainb-reflect-memory/concepts/capture/), [Drain](/ainb-reflect-memory/concepts/drain/), [Hooks reference](/ainb-reflect-memory/reference/hooks/) and the [Configuration reference](/ainb-reflect-memory/reference/configuration/).
