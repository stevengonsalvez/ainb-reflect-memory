---
title: Troubleshooting
description: Failure modes of the reflect hooks, drain, recall and memory browser, each with symptom, cause, fix and how to diagnose it.
sidebar:
  order: 2
---

reflect is built to fail quietly: hooks never block your session, and the background drain always exits 0. That keeps the agent usable, but it means problems show up as missing learnings, not as error dialogs. This page lists real failure modes found in the code and the [changelog](/ainb-reflect-memory/changelog/), with how to confirm each one.

## Where to look first

```text
┌──────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│ hook / drain │──▶│ ~/.reflect/        │──▶│ statusline badge or      │
│ fails        │   │  errors.json       │   │ `reflect timeline`       │
│ (exit 0)     │   │  last-event.json   │   │ ERR row                  │
│              │   │  drain.log         │   └──────────────────────────┘
└──────────────┘   │  logs/<hook>.log   │
                   └────────────────────┘
```

State lives in `$REFLECT_STATE_DIR` (default `~/.reflect`). The knowledge base lives in `$GLOBAL_LEARNINGS_PATH` (default `~/.learnings`).

| File in `~/.reflect` | What it holds |
|---|---|
| `errors.json` | Bounded error sink (max 200 records, same error deduped for 24 h). Drives the statusline badge. |
| `last-event.json` | Last hook failure breadcrumb (`event`, `hook`, `kind`, `detail`, `ts`). |
| `logs/<hook>.log` | Per-hook forensics log, developer-facing. |
| `drain.log` | Everything the background drain did, rotated at 10 MB to `drain.log.1`. |
| `drain-cost.jsonl` | One event per drain entry: outcome, tokens, cost, turns. |
| `pending_reflections.jsonl` | The queue of transcripts waiting to be drained. |
| `poison-reflections.jsonl` | Entries the drain gave up on, archived verbatim. |
| `retry-count.jsonl` | Per-entry retry counters. |
| `drain.lock.d/` | Atomic lock directory (contains `pid`). |
| `drain.last-run` | Epoch seconds of the last drain start (debounce). |
| `maintenance-watch.log` | Output of the maintenance watchdog. |

Quick triage:

```bash
reflect errors count                                  # unacked error count (the badge number)
jq -r '.errors[] | select(.acked|not) | "\(.ts) \(.kind) \(.message)"' ~/.reflect/errors.json
reflect timeline --explain ERR                        # last 2 h of unacked errors
tail -n 60 ~/.reflect/drain.log                       # what the drain just did
cat ~/.reflect/last-event.json                        # last hook failure, if any
```

Each error record has `id`, `ts`, `severity` (`error`, `warn`, `info`), `source`, `kind`, `message`, `context`, `count` and `acked`. After fixing the cause, clear the badge with `reflect errors ack` (or `/reflect:errors-ack`, which triages first).

:::note
`reflect errors` has three subcommands: `count`, `ack [ids...]` and `append`. To list entries, read `errors.json` as shown above.
:::

## Hooks are silent by design

**Symptom.** Recall injects nothing, or learnings stop appearing, and no error is shown anywhere in the session.

**Cause.** Every reflect hook must exit 0 on any uncaught exception, so a broken hook never blocks Claude Code, Codex or Copilot. On failure the hook writes a breadcrumb to `last-event.json` and a line to `logs/<hook>.log`, then emits empty output. Credentials in exception text are masked before being written.

**Diagnose.**

```bash
cat ~/.reflect/last-event.json
ls -t ~/.reflect/logs/ | head
tail -n 40 ~/.reflect/logs/<hook>.log
```

The harness status line reads `last-event.json` to render a recall-failed or reflect-failed fragment, so a persistent fragment there is the first sign.

**Recall also returns empty on purpose** (not a failure) when any of these hold:

| Condition | Why |
|---|---|
| Session `cwd` is `$HOME` | No project context to build a query from. |
| `uv` is not on the hook's `PATH` | The recall hook resolves `uv` once at load and falls back to an empty emit. Hooks launched from launchd or IDE subprocesses often have a trimmed `PATH`. |
| The recall script is not found | Plugin layout problem, see [hooks not registering](#hooks-not-registering-or-skills-0). |
| The recall subprocess times out | Cold model load can take 11 to 16 s. The default cap is `REFLECT_RECALL_TIMEOUT=30`. |

## Hooks not registering, or Skills (0)

**Symptom.** Nothing fires at all, or `Unknown command: /reflect` appears in drain logs, or slash commands such as `/reflect:errors-ack` are not found.

**Causes seen in releases.**

| Release | Cause | Effect |
|---|---|---|
| 5.2.1 | Root manifest dropped the `skills` array; skills live at `plugin/skills/`, which is not an auto-discovery path | Plugin registered **Skills (0)** while 13 hooks kept loading. Every `claude -p "/reflect ..."` came back `Unknown command`, exit 0, zero turns. |
| 5.0.3 | Skill `name:` fields carried a redundant `reflect:` prefix | Commands registered as `/reflect:reflect-errors-ack`. |
| 5.0.2 | Whole-repo marketplace install resolved `CLAUDE_PLUGIN_ROOT` to the repo root, where no `.claude-plugin/plugin.json` existed | No lifecycle hooks fired. |

**Fix.** Update to the current plugin (5.2.5). On Codex or Copilot, re-run the adapter install from the updated checkout:

```bash
python3 plugin/adapters/codex/codex_adapter.py install
python3 plugin/adapters/copilot/copilot_adapter.py install
```

**Diagnose.** Run `claude plugin details` and check that the skill and hook counts are not zero. Since 5.2.1, a drain run that makes zero model turns is recorded as outcome `fail_unknown_command` and raises the error kind `drain_unknown_command`; the drain aborts and leaves the queue intact.

CI guards this with `scripts/check_plugin_contract.py`, which checks that every `${CLAUDE_PLUGIN_ROOT}` path in the manifests exists.

## Hooks fail with "No requires-python value found"

**Symptom.** Hooks exit 2 with `No requires-python value found in the workspace` in repos that have a `pyproject.toml`.

**Cause.** `uv run <script>` could drop into project mode and ignore the script's own metadata when the working directory held a `pyproject.toml` without `requires-python`. Fixed in 5.0.1: all hook manifests now use `uv run --script`.

**Fix.** Upgrade to 5.0.1 or later. Check that your hook commands read `uv run --script ...`.

## The learning loop produced nothing for days

**Symptom.** Sessions run, statusline looks healthy, but no new learnings appear. `drain.log` shows `daily cap reached`.

```text
daily cap reached (today=20 >= 20; successes=0 failures=20); exiting
  WARNING: today's ENTIRE budget was burned by failures (0 successes). This is an outage, not throttling.
```

**Cause.** The drain limits itself to `REFLECT_DRAIN_DAILY_MAX` entries per UTC day (default 20). If every entry failed, the cap looks like ordinary throttling. This hid a 10-day outage in 5.2.4. Since then the log splits `successes=` from `failures=` and a cap reached by failures alone raises the error kind `drain_budget_all_failures`.

**Diagnose.**

```bash
grep -E "daily cap|WARNING|SIZE QUARANTINE|poison|NO-OP" ~/.reflect/drain.log | tail -n 20
```

Then group spend by outcome. Use the `/reflect:cost` skill (for example `/reflect:cost 7d`), or run the reporter directly from a checkout:

```bash
uv run plugin/scripts/reflect_cost.py --since 7d --by outcome
```

:::note
The drain's own warning text suggests `reflect cost --by outcome`. There is no `reflect cost` subcommand in the CLI; use the skill or the script above.
:::

### Drain outcomes

| Outcome in `drain-cost.jsonl` | Meaning | Charges daily budget? |
|---|---|---|
| `ok` | Learnings written; entry removed from queue | yes |
| `skip_<reason>` | Gate skipped the transcript (no signal, reflect-on-reflect, clean session) | no |
| `stale` | Transcript no longer on disk | no |
| `poison` | Entry exceeded `REFLECT_DRAIN_MAX_RETRIES` (default 3); archived | no |
| `quarantine_oversized` | Writer rejected input as too large; archived | no (since 5.2.4) |
| `poison_writer_drift` | Writer produced `REFLECT_DRAIN_INVALID_THRESHOLD` (default 3) consecutive invalid outputs; archived | yes |
| `poison_budget` | Run used more than `REFLECT_DRAIN_TOKEN_MAX` tokens (default 2,000,000); archived | yes |
| `poison_timeout_exit_N` | No output after the timeout retry budget; archived | yes |
| `fail_no_output_exit_N`, `fail_is_error`, `fail_exit_N` | Retryable failure; entry moves to the queue tail | yes |
| `partial_max_turns` | Hit the turn cap; treated as terminal partial progress and removed | yes |
| `fail_unknown_command` | Zero model turns; install-level fault; run aborts, queue intact | yes |
| `quota_deferred` | Subscription quota near limit; whole queue deferred | no |

## Oversized transcripts

**Symptom.** `drain.log` shows `SIZE QUARANTINE`, the error `drain_oversized_input` appears, and big sessions never yield learnings.

**Cause.** A multi-megabyte transcript (about 1.5 MB measured: 367K tokens raw) exceeds the writer's 200K context, of which roughly 64K is already spent on the child's baseline. The writer answers `Prompt is too long` before doing any work. In 5.2.4 and earlier this was charged to the daily cap and starved healthy entries.

**What the code does now.**

- The cascade slices transcripts to signal-bearing windows. When no slice exists, the drain caps the input at `REFLECT_DRAIN_MAX_INPUT_CHARS` (default 60000) using a head-plus-tail window, so the end of the session (where corrections live) survives.
- Size failures are quarantined with zero budget cost, so the queue advances.
- Since 5.2.5 the default writer is single-shot `extract`, whose context cannot grow mid-run. The legacy `agentic` loop re-sends its growing conversation each turn and can hit the context wall regardless of input bounding.

**Fix.** Upgrade to 5.2.5. Check you have not opted back into the old loop:

```bash
echo "${REFLECT_DRAIN_WRITER:-extract}"      # should print extract
echo "${REFLECT_DRAIN_CASCADE:-1}"           # 0 disables slicing; leave at 1
```

Quarantined entries are archived verbatim in `~/.reflect/poison-reflections.jsonl` (one JSON entry per line), so the transcript path and trigger are recoverable.

## Drain stuck, locked or never starting

**Symptom.** Queue keeps growing, no recent `drain start` in `drain.log`, or the log says `another drain is running`.

| Log line or error kind | Cause | Fix |
|---|---|---|
| `another drain is running (pid=N); exiting` | A live drain holds `drain.lock.d` | Wait. If it is genuinely wedged, kill that PID, not by process name. |
| `stale lock detected (pid=N not running); reclaiming` | Previous drain crashed | None. The next drain reclaims the lock itself. |
| `debounce: last drain Ns ago (< 600s); skipping` | `REFLECT_DRAIN_DEBOUNCE_SEC` collapses bursts of session starts into one drain | Wait, or lower the value. |
| `quota gate CLOSED ... deferring queue` | Subscription quota near limit; entries stay queued | Wait. `REFLECT_QUOTA_GATE=0` disables the gate. |
| Nothing in the log at all | `REFLECT_DISABLED=1` (hard kill switch), or the SessionStart hook is not registered | Unset the variable; see [hooks not registering](#hooks-not-registering-or-skills-0). |

The lock is an atomic `mkdir` of `drain.lock.d` (macOS has no `flock`), with the owner's PID inside. A crashed drain leaves the directory behind, which is why liveness is checked by PID.

The read-only maintenance watchdog (`reflect-maintenance-watch.sh`, run by the `com.reflect.maintenance` launchd job on macOS) reports these into the error sink:

| Error kind | Severity | Raised when |
|---|---|---|
| `drain_stale_lock` | error | Lock directory exists but its PID is dead |
| `drain_running_long` | warn | Drain running longer than 900 s (`REFLECT_WATCH_DRAIN_RUNNING_SEC`) |
| `drain_no_log` | warn | Queue non-empty but `drain.log` missing |
| `drain_never_started` | warn | Queue non-empty, no `drain start` ever logged |
| `drain_not_running` | warn | Queue non-empty, last drain started over 3600 s ago (`REFLECT_WATCH_DRAIN_STALE_SEC`) |
| `drain_launchd_missing` | warn | Queue non-empty but `com.reflect.drain` is not loaded (macOS) |
| `maintenance_launchd_missing` | warn | `com.reflect.maintenance` is not loaded (macOS) |
| `ingest_never_ran` / `ingest_stale` | warn | Ingest log missing, or older than 7 days (`REFLECT_WATCH_INGEST_STALE_DAYS`) |

## Timeouts and token budgets

**Symptom.** Entries end as `poison_timeout_exit_N`, `partial_max_turns` or `poison_budget`.

**Cause and tuning.** These are deliberate circuit breakers, added after a 2026-05-31 incident where one drain run burned 41.5M tokens in 9.6 minutes.

| Variable | Default | Note |
|---|---|---|
| `REFLECT_DRAIN_TIMEOUT` | 300 s | Per-entry wall clock. Must stay above the turn budget's worst case, or a run that would stop cleanly at the turn cap is SIGTERMed and quarantined instead. |
| `REFLECT_DRAIN_MAX_TURNS` | 16 | Counts assistant messages, not tool calls. 8 was below the writer's minimum honest workflow and wrote nothing (5.2.2). |
| `REFLECT_DRAIN_TOKEN_MAX` | 2000000 | Post-hoc: a completed but over-budget run is poisoned so it is never retried. |
| `REFLECT_DRAIN_TIMEOUT_RETRIES` | 1 | No-output retries before quarantine. |
| `REFLECT_DRAIN_MAX` / `REFLECT_DRAIN_DAILY_MAX` | 3 / 20 | Entries per run / per UTC day. |

To trial a change without spending tokens, set `REFLECT_DRAIN_DRY_RUN=1`; the drain only logs what it would do. It leaves the queue, the daily cap and the ledgers untouched, so it is safe to repeat.

## Recall returns nothing, or stale results

| Symptom | Cause | Fix |
|---|---|---|
| New learnings are on disk but `reflect search` does not find them | The GraphRAG index is rebuilt by `reflect reindex`; the drain runs it after successful entries, but skips it when `reflect` is not on its `PATH` (`reindex SKIP: 'reflect' CLI not on PATH`) | `reflect reindex`, and put `reflect` (default `~/.local/bin`) on the PATH the hooks see. |
| `reindex_fail` error | `reflect reindex` exited non-zero | Run `reflect reindex` by hand and read the output. |
| `graphml_corrupt` error | Entity graph file corrupt and not repairable by truncation | `reflect reindex --force` (clears the cache and rebuilds from scratch). The drain self-heals the common doubled-close-tag corruption before reindexing. |
| Recall returns empty right after changing `REFLECT_EMBED_MODEL` | Indexing and query-time embedding must share one vector space; the recall hook also pins `HF_HUB_OFFLINE=1`, so an uncached model cannot download | Run `reflect reindex` from a shell with the variable set (only the hooks pin offline mode, so a shell can download the model), then retry. |
| Archived or re-scored notes still appear in `reflect search` | The `reflect serve` curation actions edit files only; the semantic cache is rebuilt by a full reindex | `reflect reindex`. |
| Recall slow or empty on the first session of the day | Cold model load of 10 to 30 s exceeds the hook timeout | Raise `REFLECT_RECALL_TIMEOUT` (default 30). Since 5.2.0 a persistent daemon keeps models warm. |

Per-project and branch shards fall back to the global KB when a shard has no documents (5.0.2); `RECALL_GLOBAL` still overrides.

## Missing dependencies

| Missing | What happens | Fix |
|---|---|---|
| `uv` | Hooks use `uv run --script`; the recall hook emits nothing | Install `uv` and make sure it is on the hook environment's `PATH`. |
| `reflect` CLI | Capture still works; drain logs `reindex SKIP` and new learnings are not recallable until a manual reindex. | `uv tool install --upgrade --torch-backend cpu 'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'` |
| `claude` CLI | The drain's writer cannot run | Install it, or point `REFLECT_DRAIN_CLAUDE_BIN` at it. |
| `[graph]` extra (sentence-transformers, nano-graphrag) | Search raises `sentence-transformers not installed` or `nano-graphrag not installed`. `reflect rerank` returns `{"available": false, "error": "sentence-transformers not installed (slim build)"}`. Cross-encoder rerank degrades to the legacy scoring formula. | Install with the `[graph]` extra as above. |
| Postgres extras | With both `REFLECT_PG_DSN` and `REFLECT_WORKSPACE_ID` set but the `[postgres]` extra missing, the graph engine raises an error naming the extra | Install the `[postgres]` extra, or unset the variables to use the local backend. |
| Plugin not found (for `reflect timeline`) | `reflect timeline --explain` prints `error: reflect plugin not found.` | `claude plugin install reflect@ainb-reflect-memory` |

**Disk space.** The `[graph]` extra pulls torch. The default CUDA build is about 5 GB of `nvidia-*` wheels and fails on small disks with `No space left on device`. reflect embeds on CPU, so install with `--torch-backend cpu` (about 1.5 GB), as in the command above.

**Memory.** Before 5.2.0, every `reflect search`, `embed` or `rerank` cold-booted torch and both models (about 3.5 GB RSS, 10 to 30 s), and parallel session-start recalls could OOM an 8 GB machine. Models now load once into an auto-spawned unix-socket daemon. To bypass it, set `REFLECT_NO_DAEMON=1`. `REFLECT_IDLE_TIMEOUT` (default 1800 s, 0 = never) and `REFLECT_DAEMON_TIMEOUT` (default 120 s) tune it.

## Slash commands do nothing under Codex

Skills anchor their files on `${CLAUDE_PLUGIN_ROOT}`, which Codex does not set. The Codex adapter rewrites those anchors when it copies each `SKILL.md`. Re-run `python3 plugin/adapters/codex/codex_adapter.py install` after updating. Known limitation (5.2.2): non-SKILL resource files that cite the anchor keep it literal, and the Copilot and Hermes adapters do not rewrite yet.

## Memory browser (`reflect serve`)

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 403 `forbidden: non-loopback Host` | The server only answers requests whose `Host` is `127.0.0.1`, `localhost` or `[::1]` (DNS-rebinding defence). A reverse proxy that forwards the original `Host` header is rejected. | Browse from the same machine, or tunnel with `ssh -L 8377:127.0.0.1:8377 host` and open `http://localhost:8377`. |
| HTTP 403 `forbidden: missing X-Reflect header` | A mutation was sent without the `X-Reflect` header (for example by `curl`) | Use the bundled UI, or send the header. |
| `Address already in use` | Port 8377 is taken | `reflect serve --port N`. |
| Empty list | Wrong KB | Check the path printed at startup (`reflect serve --repo PATH`, or `GLOBAL_LEARNINGS_PATH`). |
| `compress needs at least two live memories` | A compress group needs at least two notes that still exist | Select two or more notes. |
| `archive already contains <file>` | A note with the same filename is already in `archived/` | Restore or rename the existing one. |
| `compress-queue.yaml is malformed` | The queue file is not valid YAML or not a mapping; the server refuses to overwrite it | Fix or move the file. |

See the [memory browser guide](/ainb-reflect-memory/guides/memory-browser/) for the full behavior.

## Still stuck

1. Run the quick triage block at the top and note the error `kind`.
2. Re-run the failing step by hand (`reflect reindex`, or a drain with `REFLECT_DRAIN_DRY_RUN=1`).
3. Open an issue at [github.com/stevengonsalvez/ainb-reflect-memory/issues](https://github.com/stevengonsalvez/ainb-reflect-memory/issues) with the relevant `drain.log` lines and the `errors.json` records (check them for private content first).
