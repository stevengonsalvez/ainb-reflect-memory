---
title: Drain
description: How queued transcripts become learnings. The detached drain, the default single-shot extract writer, oversized transcript handling, and every cost gate.
sidebar:
  order: 3
---

The drain is the only component that turns queued transcripts into notes. It is a bash script, `plugin/hooks/reflect-drain-bg.sh`, started detached at `SessionStart`. It reads the queue that [capture](/ainb-reflect-memory/concepts/capture/) fills, shrinks each transcript for $0, makes one model call, writes notes and entity sidecars through `reflect add`, then reindexes.

As of 5.2.5 the writer is the **single-shot extract** path by default. The older agentic loop is opt-in via `REFLECT_DRAIN_WRITER=agentic` and remains an automatic fallback in two cases (below).

```
 SessionStart ─▶ (nohup reflect-drain-bg.sh &)         optional: launchd timer every 600 s
                        │
        kill switch ─▶ lock (mkdir) ─▶ debounce 600 s ─▶ daily cap ─▶ pick up to 3 entries
                                                                          │
                          ┌───────────────────────────────────────────────┘
                          ▼  per entry
   quota gate ─▶ stale? retries ≥ 3? ─▶ cascade prepare ($0)
                                         │ skip: reflect-on-reflect, no-signal, dup-signal-hash, dup-chunk-hash
                                         ▼ slice (≤ 60,000 chars) + related learnings + observations
                                  writer ─┬─ extract (default): 1 tool-free claude -p ─▶ JSON actions ─▶ reflect add / revise
                                          └─ agentic: claude -p "/reflect <slice>", ≤ 16 turns
                                         ▼
              classify output ─▶ circuit breakers ─▶ outcome row in drain-cost.jsonl ─▶ rewrite queue
                          ▼ if any entry succeeded
              graphml repair ─▶ (every 10th drain) graph maintenance ─▶ reflect reindex
```

## Start, lock, and gates

| Step | Behaviour |
|---|---|
| Trigger | `SessionStart` hook runs the script under `nohup` with a 5 second hook timeout. Claude Code, Codex, and Copilot all wire it. A launchd template at `plugin/launchd/com.reflect.drain.plist` runs it every 600 s on macOS |
| Kill switch | `REFLECT_DISABLED=1` exits before any work |
| Empty queue | Exits |
| Lock | Atomic `mkdir ~/.reflect/drain.lock.d/` with the owner pid inside. A dead owner's lock is reclaimed. A live one means this run exits |
| Debounce | `~/.reflect/drain.last-run` holds the last start epoch. Runs within `REFLECT_DRAIN_DEBOUNCE_SEC` (600) of it exit, collapsing a burst of session starts into one drain |
| Daily cap | Sums the `entries` field of today's (UTC) rows in `drain-cost.jsonl`. At `REFLECT_DRAIN_DAILY_MAX` (20) the drain exits. The log line reports `successes=N failures=M`; a cap reached with zero successes logs a WARNING and raises `drain_budget_all_failures` |
| Per-run cap | At most `REFLECT_DRAIN_MAX` (3) entries, fewer if less daily headroom remains |
| Quota gate | Before each entry the drain reads `~/.reflect/quota-state.json`. See [Quota gate](#quota-gate) |

A burst of entries that cost nothing does not eat the daily budget. Outcomes that never reach the model record `entries=0`.

## The cascade ($0 front end)

`reflect_cascade.py prepare` runs before any model spend, unless the entry is a `skill_refresh` or `REFLECT_DRAIN_CASCADE=0`.

1. **Gate.** Same check as the enqueue gate. Skips reflect-on-reflect and no-signal transcripts.
2. **Hash dedup.** If a learning with this signal set's content hash already exists, the drain skips (`dup-signal-hash`) and bumps that learning's `proof_count`.
3. **Slice.** Keeps the dialogue lines around each signal, plus 3 lines of context on each side, merges overlaps, and caps the result at 60,000 characters (about 15K tokens). Typical shrink is about 10x.
4. **Delta retain.** Chunks already reflected on in a previous drain are dropped. If every chunk is a re-run the entry is skipped (`dup-chunk-hash`).
5. **Privacy.** `<private>` spans and harness wrapper tags are stripped from the slice.
6. **Belief-revision block.** Up to 5 existing learnings related to the signals are appended, with the CREATE, UPDATE, DELETE action contract and each learning's id. A second block lists up to 10 existing consolidated observations for the scope.

Skips are permanent: the entry leaves the queue and the outcome row is `skip_<reason>` with `entries=0`.

## Writers

| | Extract (default) | Agentic (opt-in, fallback) |
|---|---|---|
| Select | `REFLECT_DRAIN_WRITER=extract` or unset | `REFLECT_DRAIN_WRITER=agentic` |
| Model calls | One. `claude -p ... --allowedTools "" --max-turns 1` | A loop: `claude -p "/reflect <target>" --max-turns 16` |
| Cost shape | Linear in slice size. Context is fixed at baseline plus slice | Grows with turns, because every turn re-sends the whole conversation |
| File and CLI work | Done by `drain_extract.py`, deterministically | Done by the model, one assistant turn each |
| Can hit `partial_max_turns` | No | Yes |
| Needs a slice | Yes | No |

Measured on two 1.5 MB transcripts from [issue #34](https://github.com/stevengonsalvez/ainb-reflect-memory/issues/34) (from the 5.2.5 changelog, real `claude`, isolated state):

| Writer | Turns | Tokens | Cost | Outcome |
|---|---|---|---|---|
| agentic | 17 | 1,503,423 | $1.07 | `partial_max_turns`, nothing kept |
| extract | 1 | 77,982 | $0.40 | ok, 3 learnings written |

The agentic path is used instead of extract when:

- No slice or bounded file exists for the entry. This happens when the cascade is off or failed and the transcript is already under the input cap.
- The entry is a `skill_refresh` task, which edits a `SKILL.md` and has no transcript to slice.

### Extract in detail

`plugin/scripts/drain_extract.py` is the whole writer.

1. Reads the slice and sends it as one prompt: "You are a knowledge-extraction function, not an agent." The model returns one JSON object, `{"actions": [...]}`. Empty `actions` is a valid answer and the entry is dropped as `ok`.
2. Parses it, tolerating a code fence or stray prose around the object. Unparseable output is a retryable failure.
3. **CREATE** (at most 12 per run, extras dropped). Renders a note with typed frontmatter (`problem`, `root_cause`, `fix`, `rule`, `confidence_num`, `entities`, `causal_relations`) and a sidecar, then runs `reflect add --force <note> --entities <sidecar>`, 60 s per call. It also records a ledger row in `reflect.db` keyed on the transcript so [`record-chunk` provenance](/ainb-reflect-memory/concepts/index-and-storage/#the-ledger-reflectdb) works. A failed ledger write is a warning, not a failure.
4. **UPDATE** and **DELETE** go through `reflect_cascade.execute_revision_actions`. UPDATE appends the session id as a source and increments `proof_count` (idempotent per source). DELETE retires the learning by setting its ledger status to `reverted`; the row stays.

Safety properties, all enforced in code:

| Property | How |
|---|---|
| A transcript cannot retire arbitrary learnings | UPDATE and DELETE target ids are honoured only if they appear after the revision-block marker in the slice. Ids quoted in the transcript body are ignored |
| Model output cannot inject YAML | Every model string is rendered as a single-line double-quoted scalar. Causal-link types are checked against a closed enum and anything else becomes `relates_to` |
| Note and sidecar agree | Both use one id, `lrn-<slug>-<hash6>` with `hash6 = sha1(title + rule)[:6]` |
| Failed writes are retried, benign drops are not | A `reflect add` failure or unparseable output keeps the transcript queued. Over-cap CREATEs, unlisted target ids, and title-less CREATEs are dropped without a retry |

The timeout is split so the write pass is not killed mid-index. The outer cap is `REFLECT_DRAIN_TIMEOUT` (300 s). The model call gets that minus 90 s (210 s by default, or 60 s if the cap is 150 s or less).

:::note
Extract CREATEs go straight to `reflect add`. The embedding-cosine twin check (threshold `REFLECT_DEDUP_THRESHOLD`, default 0.97) lives in the revise CREATE path, which the extract writer uses only for UPDATE and DELETE. Extract relies on the model preferring UPDATE, and on identical title plus rule producing the same id.
:::

## Oversized transcripts

Fixed in 5.2.4 after a ten day silent outage. Sessions had grown past about 1.5 MB, which is 367K tokens raw against a 200K context that already spends about 64K on the drain child's own baseline.

| Layer | Behaviour |
|---|---|
| Normal bound | The cascade slice, capped at 60,000 characters |
| Last-resort bound | If no slice exists and the file is larger than `REFLECT_DRAIN_MAX_INPUT_CHARS` (60,000, compared against the file size in bytes), `reflect_cascade.py bound` writes a privacy-filtered head and tail view of the dialogue. The head is a quarter of the budget, the tail three quarters, because corrections cluster at the end of a session. The tail is read straight off the file, since the gate's extractor stops from the front |
| Bounded view has no revision block | UPDATE and DELETE actions are all dropped as unlisted ids. Only CREATE can land from a bounded input |
| Size rejection | If the model still answers "prompt is too long" (or `context window`, `maximum context length`, `context length exceeded`, `conversation is too long`), the entry is archived to the poison file as `quarantine_oversized` with `entries=0`. It does not consume the daily budget and the queue advances |
| Other poison markers | `session exhausted`, `credit balance is too low` and similar take the budget-consuming path as `poison_writer_drift` |

## Cost gates

| Control | Env var | Default | Effect |
|---|---|---|---|
| Per-run cap | `REFLECT_DRAIN_MAX` | `3` | Entries per drain run |
| Daily cap | `REFLECT_DRAIN_DAILY_MAX` | `20` | Entries per UTC day, summed from `drain-cost.jsonl` |
| Debounce | `REFLECT_DRAIN_DEBOUNCE_SEC` | `600` | Minimum gap between drain runs |
| Wall clock | `REFLECT_DRAIN_TIMEOUT` | `300` | Per-entry cap on the writer call, seconds |
| Timeout retries | `REFLECT_DRAIN_TIMEOUT_RETRIES` | `1` | No-output timeouts before the transcript is quarantined |
| Turn cap | `REFLECT_DRAIN_MAX_TURNS` | `16` | Agentic writer only. Counts assistant messages, not tool calls |
| Token poison | `REFLECT_DRAIN_TOKEN_MAX` | `2000000` | After the run: a transcript whose run reported more total tokens is archived so it cannot be retried |
| Input cap | `REFLECT_DRAIN_MAX_INPUT_CHARS` | `60000` | Bound on writer input when no slice exists |
| Retry cap | `REFLECT_DRAIN_MAX_RETRIES` | `3` | Failed attempts before an entry is archived as poison |
| Writer drift | `REFLECT_DRAIN_INVALID_THRESHOLD` | `3` | Consecutive non-valid writer outputs before archiving |
| Cascade | `REFLECT_DRAIN_CASCADE` | `1` | `0` disables gate and slice |
| Writer | `REFLECT_DRAIN_WRITER` | `extract` | `agentic` selects the legacy loop |
| Model | `REFLECT_DRAIN_MODEL` | `sonnet` | `--model` alias passed to `claude -p` |
| Working dir | `REFLECT_DRAIN_CWD` | `$HOME` | cwd of the writer, so it never runs inside the triggering repo |
| Binaries | `REFLECT_DRAIN_CLAUDE_BIN`, `REFLECT_DRAIN_REFLECT_BIN` | `claude`, `reflect` on `PATH` or `~/.local/bin/reflect` | Pin a binary |
| Kill switch | `REFLECT_DISABLED` | unset | `1` makes the drain a no-op |

### Quota gate

The drain never makes an API call to check quota. After each writer run it feeds the result's `rate_limit_info` (or `429`/`529` on stderr) into `~/.reflect/quota-state.json`, and before each entry it reads that file. The gate closes when a window is `rejected`, past its warning threshold with no overage cushion, or at or above its utilization ceiling: 0.95 for `five_hour` and `overage`, 0.93 for `seven_day` and `seven_day_opus`, 0.92 for `seven_day_sonnet`. A closed gate defers the rest of the queue (`quota_deferred`, `entries=0`); entries stay queued and replay later. Snapshots expire after `REFLECT_QUOTA_TTL_SEC` (3600), so a stale reading fails open. `ANTHROPIC_API_KEY` auth is exempt. Disable with `REFLECT_QUOTA_GATE=0`; override ceilings with `REFLECT_QUOTA_UTIL_THRESHOLD`.

## Outcomes and queue effect

Each processed entry appends one row to `~/.reflect/drain-cost.jsonl` with `outcome`, `entries`, token buckets, `cost_usd`, `turns`, `model`, and `writer_class`.

| Outcome | `entries` | Queue |
|---|---|---|
| `ok` | 1 | Dropped. Includes "extracted nothing durable" |
| `skip_<reason>` | 0 | Dropped (cascade skip) |
| `stale` | 0 | Dropped (transcript missing on disk) |
| `poison` | 0 | Archived to `poison-reflections.jsonl` (retries exhausted) |
| `quarantine_oversized` | 0 | Archived to `poison-reflections.jsonl` |
| `poison_writer_drift` | 1 | Archived (threshold of invalid outputs, or a non-size poison marker) |
| `poison_budget` | 1 | Archived (over `REFLECT_DRAIN_TOKEN_MAX`) |
| `poison_timeout_exit_<n>` | 1 | Archived (no-output timeout exhausted) |
| `partial_max_turns` | 1 | Dropped. Agentic only. Treated as terminal partial progress |
| `fail_is_error`, `fail_exit_<n>`, `fail_no_output_exit_<n>` | 1 | Moved to the tail, retry count bumped |
| `fail_unknown_command` | 1 | Left in place, run aborted, no retry bump. A zero-turn exit 0 means the plugin's skills are not registered, an install fault, so it must not charge each transcript's retry budget |
| `quota_deferred` | 0 | Left in place |

Entries that fail with a retryable error move to the end of the queue so one bad transcript cannot block the rest. A transcript with 3 recorded retries is archived as `poison` on its next pick-up.

The writer's raw output is classified `valid`, `prose`, `idle`, `poisoned`, or `malformed` (`output_classifier.py`), and the category is stored as `writer_class` so `--by writer` shows writer health.

## After the loop

If at least one entry succeeded and `REFLECT_DRAIN_SKIP_REINDEX` is not `1`:

1. `graphml_repair.py --repair` validates the graph file and repairs a doubled closing tag by truncation. Unrepairable corruption raises `graphml_corrupt` and advises `reflect reindex --force`.
2. Every `REFLECT_DRAIN_MAINTAIN_EVERY` (10) reindexing drains, `graphml_repair.py --maintain` prunes orphan entities and dangling edges and relinks isolated nodes. `0` disables it.
3. `reflect reindex` (no `--force`), 300 s cap. A failure raises `reindex_fail` but does not fail the drain.

If `reflect` is not on `PATH`, notes are still written but stay invisible to recall until a manual reindex.

## Observing the drain

| What | Where |
|---|---|
| Run log | `~/.reflect/drain.log` (rotates at `REFLECT_DRAIN_LOG_MAX_BYTES`, default 10 MB, to `drain.log.1`) |
| Cost ledger | `~/.reflect/drain-cost.jsonl`. Reported by `/reflect:cost` (`plugin/scripts/reflect_cost.py --since 30d --by day\|transcript\|model\|outcome\|writer`) |
| Poisoned entries | `~/.reflect/poison-reflections.jsonl` |
| Retry counters, writer streaks | `~/.reflect/retry-count.jsonl`, `~/.reflect/writer-health.jsonl` |
| Errors raised | `/reflect:status` and `reflect errors count` (kinds such as `drain_poison`, `drain_oversized_input`, `drain_quota_deferred`, `drain_unknown_command`) |
| Dry run | `REFLECT_DRAIN_DRY_RUN=1` logs the call it would make. It does not call the model, but it still removes the entry from the queue and records a `dry_run` row that counts toward the daily cap |

:::caution
`REFLECT_DRAIN_DRY_RUN=1` is not side-effect free for the queue. In a test run against a one-entry queue, the entry was consumed. Use a copy of the state dir (`REFLECT_STATE_DIR`) when dry-running.
:::

## Why it looks like this

| Release | Change |
|---|---|
| 4.0.0 | Cost rearchitecture: cascade gate and slice, Sonnet by default, hard caps, atomic lock, debounce, `reflect cost` |
| 5.2.1 | A zero-turn run is `fail_unknown_command`, not `ok` (skills had stopped registering and the drain scored every run a success for 11 days) |
| 5.2.2 | Turn cap raised 8 to 16 and timeout 180 to 300 s, because 8 turns could never finish the writer's minimum workflow |
| 5.2.3 | Single-shot extract writer, opt-in |
| 5.2.4 | Oversized inputs bounded, size rejections quarantined with `entries=0` so they cannot starve the daily budget |
| 5.2.5 | `REFLECT_DRAIN_WRITER` defaults to `extract` |

The full text is in the [Changelog](/ainb-reflect-memory/changelog/). The failure modes behind each fix are also exercised by `plugin/tests/test_drain_*.py`, which run the real script against a stub `claude`.
