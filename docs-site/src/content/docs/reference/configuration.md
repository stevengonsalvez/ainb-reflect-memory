---
title: Configuration reference
description: Every reflect.toml key and every environment variable the plugin and CLI read, with type, default, effect, config search order and precedence, verified against the code.
sidebar:
  order: 3
---

reflect is configured in two ways: a layered TOML file (`reflect.toml`) and environment variables. Most day-to-day tuning is environment variables, because the hooks and the drain run as separate processes that the harness launches. Most `reflect.toml` keys are either read by the ingest and discovery scripts or are reserved. Both are listed in full below, with the status of each key checked against the code that consumes it.

:::note
Two different loaders read `reflect.toml`, and they search different places. See [Search order](#search-order-and-precedence). Hook behavior is in the [Hooks reference](/ainb-reflect-memory/reference/hooks/); CLI flags are in the [CLI reference](/ainb-reflect-memory/reference/cli/).
:::

## Search order and precedence

### Plugin-side loader (`plugin/scripts/reflect_config.py`)

Used by ingest, discovery, the DB layer, the mode loader, the signal detector, the cascade and the lifecycle-event emitter. Layers are **deep-merged**, later wins:

| # | Layer | Path |
|---|---|---|
| 1 | Built-in defaults | hard-coded in `_BUILTIN_DEFAULTS` |
| 2 | Plugin default | `reflect.toml` next to the plugin (`plugin/reflect.toml`) |
| 3 | User override | `~/.reflect/reflect.toml` |
| 4 | Project override | `./.reflect.toml` in the current working directory |
| 5 | Environment variables | the 17 `REFLECT_*` names in [Config overlay variables](#config-overlay-variables) |

A missing or malformed file is skipped silently (an empty table). Values from the environment are cast to the key's type: lists split on commas, booleans are false for `0`, `false`, `no`, `off` or empty, ints and floats are parsed (a malformed number raises). The result is cached per process.

The user override path is fixed at `~/.reflect/reflect.toml`; it does **not** follow `$REFLECT_STATE_DIR`.

### Engine-side loader (`src/reflect_kb/reflect_config.py`)

Used only by `reflect issues run`, which reads the `[issues]` table. **First existing file wins; there is no merging.**

| # | Path |
|---|---|
| 1 | `$REFLECT_CONFIG` |
| 2 | `$REFLECT_STATE_DIR/reflect.toml` |
| 3 | `~/.reflect/reflect.toml` |
| 4 | bundled `plugin/reflect.toml`, found by walking up from the module (editable checkouts only) |

A missing or malformed file yields `{}`, so the CLI always has defaults. For `reflect issues run`, an explicit flag beats `[issues]`, which beats the built-in default.

### Active mode precedence

`mode_loader.py` resolves the active mode in this order: `REFLECT_MODE` env, then `<project>/.reflect/config.json` (`{"mode": "<id>"}`, written by `mode_loader.py set <id>`), then the `mode` key of the merged TOML cascade, then `engineering`. The project root is `$CLAUDE_PROJECT_DIR`, else the nearest `.git` ancestor, else cwd.

### Inspect the effective config

```bash
python3 plugin/scripts/reflect_config.py     # merged plugin-side config as JSON
```

Run it from the project directory so `./.reflect.toml` is included. For an installed plugin the script sits under the plugin root (`${CLAUDE_PLUGIN_ROOT}/scripts/reflect_config.py`).

## reflect.toml keys

Status column: **live** means code reads the key; **reserved** means it is parsed and merged but no code in this repo reads it, so changing it has no effect (use the env var named in the Effect column where one exists); **env-only** means it exists only as a built-in default plus an env overlay.

### Top level

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `mode` | string | `"engineering"` | live | Selects `references/modes/<id>.json`: learning types, concepts, signal patterns, writer prompts and locale. Single-level `parent--override` inheritance (`engineering--zh` inherits `engineering`). Shipped modes: `engineering`, `engineering--zh`. Overridden by `.reflect/config.json` and `REFLECT_MODE`. |

### `[storage]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `db_path` | path | `"~/.reflect/reflect.db"` | live | SQLite DB for the structured store (`reflect_db.py`, `state_manager.py`). `~` and `$VAR` are expanded. Env `REFLECT_DB_PATH`. |
| `artifacts_dir` | path (relative to project) | `"docs/solutions"` | live | Where `/reflect` writes per-project solution notes (`output_generator.py`). Env `REFLECT_ARTIFACTS_DIR`. |

### `[discovery]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `enabled_providers` | list of strings | `["claude", "codex", "copilot", "gemini", "hermes"]` | live (partial) | Providers `memory_discovery.py` scans for ingest. Only `claude`, `codex`, `copilot`, `gemini` have a provider class; `hermes` is silently skipped. A provider is also skipped when its home directory does not exist. Env `REFLECT_PROVIDERS` (comma-separated). |
| `staleness_days` | int | `30` | reserved | Intended stale-source threshold. Nothing reads it. Env `REFLECT_STALENESS_DAYS` feeds the same unread key. |

:::note
The code-level built-in default for `enabled_providers` is `["claude", "codex", "gemini"]`. The shipped `plugin/reflect.toml` replaces that list (lists are replaced, not merged), so the effective default is the five-name list above.
:::

### `[providers.*]`

Each provider class reads its own table at construction. Paths are expanded with `~` and `$VAR`.

| Table | Key | Default | Status | Effect |
|---|---|---|---|---|
| `[providers.claude]` | `projects_dir` | `"~/.claude/projects"` | live | Root scanned for Claude auto-memory. |
| | `memory_pattern` | `"*/memory/*.md"` | live | Glob under `projects_dir`. Matches the consolidated `MEMORY.md` index **and** every atomic per-fact file (`feedback_*.md`, `project_*.md`, and so on). |
| `[providers.codex]` | `home_dir` | `"~/.codex"` | live | Provider is available only if this directory exists. |
| | `memories_dir` | `"~/.codex/memories"` | live | `*.md` files ingested from here. |
| | `agents_md` | `"~/.codex/AGENTS.md"` | live | Global instructions file, ingested if present. |
| `[providers.copilot]` | `home_dir` | `"~/.copilot"` | live | Availability check and path safety prefix. |
| | `agents_md` | `"~/.copilot/AGENTS.md"` | live | Global instructions file. |
| `[providers.gemini]` | `home_dir` | `"~/.gemini"` | live | Availability check and path safety prefix. |
| | `global_md` | `"~/.gemini/GEMINI.md"` | live | Global instructions file. |
| `[providers.hermes]` | `home_dir` | `"~/.hermes"` | reserved | No Hermes provider class exists. |

### `[indexers.graphrag]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `cli_path` | string | `"reflect"` | reserved | Intended indexer binary. Callers resolve `reflect` from `PATH` directly (`shutil.which("reflect")`); nothing reads this key. |
| `auto_sidecar` | bool | `true` | reserved | Nothing reads it. |

### `[policies]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `auto_approve_threshold` | float | `0.8` | reserved | A helper in `signal_detector.py` reads it but has no caller. Env `REFLECT_AUTO_APPROVE`. |
| `retention_days` | int | `90` | reserved | Nothing reads it. Env `REFLECT_RETENTION_DAYS`. Per-learning expiry uses the `forget_after` field instead. |
| `max_memory_lines` | int | `200` | reserved | Nothing reads it. |

### `[recall.cross_encoder]`

All four keys are declarative only: `recall.py` runs as a standalone script and reads **env vars**, not this table. The `REFLECT_RECALL_CE_*` overlay variables write into the config dict and nothing else, so they do not change recall behavior.

| Key | Type | Default | Status | Real control |
|---|---|---|---|---|
| `enabled` | bool | `true` | reserved | `RECALL_CROSS_ENCODER=0` disables rerank. |
| `model` | string | `"cross-encoder/ms-marco-MiniLM-L-6-v2"` | reserved | `REFLECT_CE_MODEL` (engine). |
| `candidates` | int | `20` | reserved | Hard-coded to 20 in `recall.py`. |
| `timeout_s` | int | `60` | reserved | `RECALL_CE_TIMEOUT` (default 60). |

### `[recall.boost]`

Also declarative; the live control is the env var. Each boost is bounded: a hit's score is multiplied by `1 + alpha * (norm - 0.5)`, so `0.2` is at most plus or minus 10 percent. `0` disables.

| Key | Type | Default | Status | Real control |
|---|---|---|---|---|
| `project_affinity_alpha` | float | `0.2` | reserved | `RECALL_PROJECT_ALPHA` (default 0.2). Same-project hits gain up to 10 percent; cross-project hits are unchanged. |
| `domain_affinity_alpha` | float | `0.2` | reserved | `RECALL_DOMAIN_ALPHA` (default 0.2). Applies when a `--domain-hint` matches the learning's `domain`. |
| `authority_alpha` | float | `0.1` | reserved | `RECALL_AUTHORITY_ALPHA` (default 0.1). `law` and `promoted` notes rank above `advisory`; `archived` takes the floor. |

### `[recall.arm.<name>]` (built-in default, absent from the shipped file)

Calibrated per-arm out-of-domain floors (`reflect calibrate-thresholds`). They exist only in `_BUILTIN_DEFAULTS` and the env overlay. `recall.py` ignores them: its runtime default for every arm is `0` (off), and it only changes when you export the matching `RECALL_ARM_<NAME>_MIN_SCORE`.

| Key | Type | Built-in default | Status | Real control |
|---|---|---|---|---|
| `recall.arm.vector.min_score` | float 0 to 1 | `0.1` | reserved | `RECALL_ARM_VECTOR_MIN_SCORE` (runtime default 0) |
| `recall.arm.bm25.min_score` | float 0 to 1 | `0.15` | reserved | `RECALL_ARM_BM25_MIN_SCORE` (runtime default 0) |
| `recall.arm.graph.min_score` | float 0 to 1 | `0.0` | reserved | `RECALL_ARM_GRAPH_MIN_SCORE` (runtime default 0) |
| `recall.arm.temporal.min_score` | float 0 to 1 | `0.05` | reserved | `RECALL_ARM_TEMPORAL_MIN_SCORE` (runtime default 0) |

### `[cascade]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `dedup_threshold` | float | `0.97` | live | Per-ingest semantic dedup. A revise CREATE whose embedding cosine to an existing learning is at or above this value is held for a focused merge-or-keep verdict instead of landing as a near-duplicate. `>= 1.0` disables the probe. Env `REFLECT_DEDUP_THRESHOLD` wins over the file; an unparseable value falls back to `0.97`. |

### `[telemetry]`

| Key | Type | Default | Status | Effect |
|---|---|---|---|---|
| `enabled` | bool | `true` | reserved | Nothing reads it. |
| `log_level` | string | `"info"` | reserved | Nothing reads it. Env `REFLECT_LOG_LEVEL`. |

### Tables read by code but not in the shipped file

| Table | Loader | Keys | Effect |
|---|---|---|---|
| `[issues]` | engine-side | `repo` (default: `gh`'s cwd repo), `limit` (`20`), `model` (`"sonnet"`), `label` (`"reflect"`), `title_prefix` (`"reflect: "`) | Defaults for `reflect issues run`. Flags override. Empty string disables `label` and `title_prefix`. |
| `[events.on]` | plugin-side | `<event> = "<shell command>"` for `learning.created`, `learning.updated`, `skill.refreshed`, `consolidation.completed` | Runs the command (through the shell) after the event is appended to `events.jsonl`. Receives `REFLECT_EVENT` and `REFLECT_EVENT_PAYLOAD` (JSON) in its environment. Env `REFLECT_EVENTS_ON_<EVENT>` (dots become underscores, upper case) wins over the file. A failing hook never breaks the emitter. |

```toml
# ~/.reflect/reflect.toml
mode = "engineering"

[issues]
repo = "myorg/myrepo"
limit = 10

[events.on]
"learning.created" = "notify-send reflect \"$REFLECT_EVENT\""
```

## Environment variables

Variables are read at call time unless noted. Booleans written "truthy" accept `1`, `true`, `yes`, `on` (case-insensitive). Booleans written "`0` disables" are on unless the value is exactly `0`.

### Config overlay variables

Plugin-side only. These overwrite the matching `reflect.toml` key after all files are merged. Malformed numbers raise.

| Variable | Type | Overrides |
|---|---|---|
| `REFLECT_MODE` | string | `mode` (also read directly by `mode_loader.py`, which the drain writer and recall both use) |
| `REFLECT_DB_PATH` | path | `storage.db_path` |
| `REFLECT_ARTIFACTS_DIR` | path | `storage.artifacts_dir` |
| `REFLECT_PROVIDERS` | comma list | `discovery.enabled_providers` |
| `REFLECT_STALENESS_DAYS` | int | `discovery.staleness_days` (reserved) |
| `REFLECT_LOG_LEVEL` | string | `telemetry.log_level` (reserved) |
| `REFLECT_RETENTION_DAYS` | int | `policies.retention_days` (reserved) |
| `REFLECT_AUTO_APPROVE` | float | `policies.auto_approve_threshold` (reserved) |
| `REFLECT_RECALL_CE_ENABLED` | bool | `recall.cross_encoder.enabled` (reserved) |
| `REFLECT_RECALL_CE_MODEL` | string | `recall.cross_encoder.model` (reserved) |
| `REFLECT_RECALL_CE_CANDIDATES` | int | `recall.cross_encoder.candidates` (reserved) |
| `REFLECT_RECALL_CE_TIMEOUT` | int | `recall.cross_encoder.timeout_s` (reserved) |
| `REFLECT_RECALL_PROJECT_ALPHA` | float | `recall.boost.project_affinity_alpha` (reserved) |
| `REFLECT_RECALL_ARM_{VECTOR,BM25,GRAPH,TEMPORAL}_MIN_SCORE` | float | `recall.arm.<name>.min_score` (reserved; four variables) |

### State and paths

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_CONFIG` | unset | Explicit `reflect.toml` path for the engine-side loader (first in its search order). Not read by the plugin-side loader. See [Search order](#search-order-and-precedence). |
| `REFLECT_STATE_DIR` | `~/.reflect` | Root for the queue, armed files, ledgers, logs, errors, cost log, drain lock, models cache and fleet ledger. Read by every hook, the drain, the engine and recall. |
| `GLOBAL_LEARNINGS_PATH` | `~/.learnings` | Knowledge-base root; learnings live under `documents/`. Setting it **pins** recall to that KB and disables per-project shard selection. Also read by the CLI, the corpus filter and the maintenance watchdog. |
| `REFLECT_LEARNINGS_DIR` | `~/.learnings/documents` | Directory hooks write mini-learnings and TodoWrite learnings into. Note this is the `documents/` directory itself, not the KB root. |
| `RECALL_LEARNINGS_ROOT` | `~/.learnings` | Root that per-project shards (`shards/<project>/`, `shards/<project>/branches/<branch>/`) live under. |
| `REFLECT_METRICS_PATH` | `~/.learnings/metrics.jsonl` | Recall follow-up metrics, `reflect_cost.py`, and Hermes shadow telemetry. The engine's own `reflect metrics` writer uses a fixed path and does not honor this. |
| `REFLECT_SKILLS_DIR` | `~/.claude/skills` | Directory indexed for the skills tier of SessionStart recall. |
| `REFLECT_MODES_DIR` | `<plugin>/references/modes` | Directory of mode JSON files. |
| `REFLECT_POLICY_FILE` | unset | Extra policy-rules JSONL, read before `policy-rules.jsonl` and `permission-policy.jsonl` in the state dir. See [Policy rules file](/ainb-reflect-memory/reference/hooks/#policy-rules-file). |
| `CLAUDE_PROJECT_DIR` | unset (set by Claude Code) | Project root for the hooks, mode loader and artifact paths. Falls back to the stdin `cwd`, then the process cwd. |

### Kill switches and hook behavior

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_DISABLED` | unset | `1` makes the drain, the idle sweep and the maintenance watchdog exit immediately. It does **not** disable recall hooks or the `Stop`, `SessionEnd`, `SubagentStop` and `PreCompact` queue producers. |
| `REFLECT_AUTO_REFLECT` | on | `0`, `false`, `no` or `off` makes `precompact_reflect.py` a no-op. Read by no other hook. |
| `REFLECT_POSTCOMPACT_RESET_DEDUPE` | unset | Exactly `1`: `PostCompact` deletes the session's `session-injected` dedupe file. |
| `REFLECT_SLOTS` | off (truthy) | Memory slots. Injects the slots block ahead of recall at SessionStart, and runs the deterministic slot-reflect pass at `Stop`. |
| `REFLECT_TIERED_INJECT` | off (truthy) | Enables the skills tier (a strong skill hit replaces raw recall) and the one-line CONVENTIONS pointer at SessionStart. |
| `REFLECT_SKILL_TIER_MIN_SCORE` | `2.0` | Minimum skills-index score that counts as a strong hit (name or tag hit scores 2.0, summary hit 1.0). |
| `REFLECT_CONVENTIONS_SYMLINK` | off (truthy) | Also create a `CONVENTIONS.md` symlink in the project root. Writes into your repo, so off by default. |
| `REFLECT_RECALL_TIMEOUT` | `30` | Seconds allowed for the recall subprocess in the SessionStart and UserPromptSubmit hooks. Invalid values fall back to 30. |
| `REFLECT_RECALL_MIN_OVERLAP` | `0.2` in the SessionStart hook, `0.0` in `recall.py` | Out-of-domain gate: minimum query-term coverage of the top hit. A non-numeric value crashes the SessionStart hook at import. |
| `REFLECT_RECALL_MAX_TOKENS` | `0` | Token budget for the SessionStart block (`0` means max-chars only). Must be an integer. |
| `REFLECT_SUBAGENT_RECALL_TIMEOUT` | `5` | Seconds for the SubagentStart recall. Must be numeric. |
| `REFLECT_SUBAGENT_RECALL_LIMIT` | `3` | Learnings injected at SubagentStart. |
| `REFLECT_SUBAGENT_RECALL_MAX_CHARS` | `1500` | Character budget at SubagentStart. |
| `REFLECT_SUBAGENT_CONTEXT` | unset | If set (even to empty), used verbatim instead of running recall at SubagentStart. For tests and fixed context. |
| `REFLECT_HARNESS` | unset | `copilot` selects Copilot's `{"additionalContext": ...}` envelope. The Copilot adapter prefixes it on every hook command; the Hermes shim sets `hermes` for its child process. Recorded on queue entries written by the shared helper (`unknown` when unset). |

### Recall engine

Read by `plugin/skills/recall/scripts/recall.py`. These tune ranking and are safe to leave alone. See [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/) and [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/).

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_RECALL_LIMIT` | `10` | Default result count for `recall.py` (the hooks pass 3). |
| `REFLECT_RECALL_MAX_CHARS` | `2000` | Default character budget for `recall.py` (the hooks pass 1500). |
| `REFLECT_RECALL_DEBUG` | unset | Any non-empty value prints cache and debug warnings to stderr. |
| `REFLECT_RECALL_HYDE` | unset | Exactly `1`: expand the query with a hypothetical answer sentence from `claude -p` (uses `REFLECT_DRAIN_MODEL`). Falls back to the raw query on any failure. |
| `RECALL_GRAPH_ARM` | on | `0` disables the graph-expansion arm. |
| `RECALL_CROSS_ENCODER` | on | `0` disables cross-encoder rerank (falls back to the formula). |
| `RECALL_CE_TIMEOUT` | `60` | Seconds for the rerank call. |
| `RECALL_MMR` | on | `0` disables MMR diversity selection. |
| `RECALL_MMR_LAMBDA` | `0.7` | `1.0` is pure relevance, `0.0` pure diversity. |
| `RECALL_EMBED_TIMEOUT` | `60` | Seconds for the embedding call used by MMR. |
| `RECALL_TEMPORAL` | on | `0` disables query date-phrase extraction. |
| `RECALL_TEMPORAL_ARM` | on | `0` disables the temporal retrieval arm. |
| `RECALL_BITEMPORAL_EDGES` | on | `0` disables the supersession filter on graph edges for dated queries. |
| `RECALL_FUZZY_CACHE` | on | `0` disables the fuzzy (Jaccard) cache tier. |
| `RECALL_FUZZY_THRESHOLD` | `0.85` | Minimum token-set similarity for a fuzzy cache hit (0 to 1). |
| `RECALL_GAP_LOG` | on | `0` stops logging zero-result queries as knowledge gaps. |
| `RECALL_FOLLOWUP` | on | `0` disables follow-up-rate tracking. |
| `RECALL_FOLLOWUP_WINDOW_SECONDS` | `30` | Follow-up detection window (floor 1). |
| `RECALL_ECONOMICS` | on | `0` hides per-learning and total token-economics numbers. |
| `RECALL_ARM_{VECTOR,BM25,GRAPH,TEMPORAL}_MIN_SCORE` | `0` (off) | Per-arm query-term-coverage floor before fusion, clamped 0 to 1. |
| `RECALL_CONFIDENCE_ALPHA`, `RECALL_RECENCY_ALPHA`, `RECALL_TAG_ALPHA` | `0.2` each | Boost strength (clamped 0 to 2; `0` disables). |
| `RECALL_PROOF_ALPHA`, `RECALL_AUTHORITY_ALPHA` | `0.1` each | Proof-count and authority-tier boosts. |
| `RECALL_PROJECT_ALPHA`, `RECALL_DOMAIN_ALPHA`, `RECALL_SPECULATIVE_ALPHA` | `0.2` each | Project and domain affinity boosts; speculative notes take the floor (minus 10 percent). |
| `RECALL_GLOBAL` | off (truthy) | Search the pooled `~/.learnings` KB instead of the current project shard. |
| `RECALL_ALL_BRANCHES` | off (truthy) | Search every branch of the current project instead of the current branch sub-shard. |
| `RECALL_BRANCH` | current git branch | Override the branch used to pick the sub-shard. `main`, `master` and detached HEAD map to the project-level shard. The SessionStart hook sets it for its child processes. |

Shard precedence, highest first: an explicit `GLOBAL_LEARNINGS_PATH`, then `--global` or `RECALL_GLOBAL`, then the current project shard, then the pooled KB.

### Drain

Read by `plugin/hooks/reflect-drain-bg.sh` and a few helper scripts. See [Drain](/ainb-reflect-memory/concepts/drain/) for the pipeline these cap.

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_DRAIN_MAX` | `3` | Max queue entries per drain run. |
| `REFLECT_DRAIN_DAILY_MAX` | `20` | Max entries per UTC day. |
| `REFLECT_DRAIN_MAX_RETRIES` | `3` | Per-entry retries before the transcript is poisoned (archived, never retried). |
| `REFLECT_DRAIN_TIMEOUT` | `300` | Per-entry `claude -p` wall-clock cap, seconds. |
| `REFLECT_DRAIN_TIMEOUT_RETRIES` | `1` | Timeout or no-output retries before quarantine. |
| `REFLECT_DRAIN_MAX_TURNS` | `16` | Turn budget per `claude -p` run. |
| `REFLECT_DRAIN_TOKEN_MAX` | `2000000` | A completed run reporting more total tokens than this is poisoned so it cannot be retried. |
| `REFLECT_DRAIN_MODEL` | `sonnet` | `--model` alias for the writer. |
| `REFLECT_DRAIN_WRITER` | `extract` | `extract`: one tool-free call that emits JSON actions, executed deterministically (default since 5.2.5). `agentic`: legacy multi-turn `/reflect` loop. Entries with no slice fall back to `agentic`. |
| `REFLECT_DRAIN_CASCADE` | `1` | Gate and slice each transcript before spending. Any value other than `1` disables it. |
| `REFLECT_DRAIN_MAX_INPUT_CHARS` | `60000` | Hard cap on writer input when no cascade slice bounded it; larger inputs are cut to a head-plus-tail window. |
| `REFLECT_DRAIN_DEBOUNCE_SEC` | `600` | Minimum seconds between drain runs, collapsing a burst of session starts. The launchd plist sets it to `0`. |
| `REFLECT_DRAIN_CWD` | `$HOME` | Working directory for `claude -p` (neutral, not the triggering repo). |
| `REFLECT_DRAIN_CLAUDE_BIN` | `claude` | Path to the `claude` binary. |
| `REFLECT_DRAIN_REFLECT_BIN` | `reflect` on `PATH`, else `~/.local/bin/reflect` | Path to the `reflect` binary for the writer's `reflect add` calls. |
| `REFLECT_DRAIN_INVALID_THRESHOLD` | `3` | Consecutive non-valid writer outputs before the writer-drift breaker poisons the transcript. |
| `REFLECT_DRAIN_MAINTAIN_EVERY` | `10` | Run the graph-maintenance sweep (orphan and stale prune, relink) once per N reindexing drains. `0` disables. |
| `REFLECT_DRAIN_SKIP_REINDEX` | `0` | `1` skips the incremental reindex after a drain. |
| `REFLECT_DRAIN_LOG_MAX_BYTES` | `10485760` | `drain.log` rotation threshold. |
| `REFLECT_DRAIN_DRY_RUN` | `0` | `1` logs what would run and never calls `claude -p`. |
| `REFLECT_DRAIN_NO_DELEGATE` | `0` | Internal. `1` stops a Codex-installed copy of the drain from delegating to the newest Claude plugin-cache copy. |
| `REFLECT_QUOTA_GATE` | `1` | Any value other than `1` skips the subscription-quota gate. When the gate is on, the queue is deferred (`quota_near_limit`) near a limit and replays later. |
| `REFLECT_QUOTA_TTL_SEC` | `3600` | Freshness window for a quota snapshot; a stale snapshot opens the gate. |
| `REFLECT_QUOTA_UTIL_THRESHOLD` | per-window | Single ceiling (greater than 0, up to 1) applied to every window. Defaults: five-hour 0.95, seven-day 0.93, seven-day Opus 0.93, seven-day Sonnet 0.92, overage 0.95. |
| `REFLECT_QUIET_INSTALL_WARNING` | `0` | `1` suppresses the missing-`reflect`-CLI notice. |
| `REFLECT_SYNTHESIS_AUTO_THRESHOLD` | `30` | New learnings since the last pass that trigger the synthesis job early. |
| `REFLECT_DEDUP_THRESHOLD` | `0.97` | See `[cascade].dedup_threshold`. |
| `ANTHROPIC_API_KEY` | unset | Read by the quota gate only: when set, the gate treats you as API-billed and never defers. |

### Idle sweep and maintenance watchdog

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_IDLE_DISABLED` | `0` | `1` makes `idle_reflect.sh` a no-op. |
| `REFLECT_IDLE_THRESHOLD_SEC` | `600` | Quiet seconds before a session counts as idle. |
| `REFLECT_IDLE_MAX_AGE_SEC` | `86400` | Ignore transcripts older than this (stops a fresh install backfilling dead sessions). |
| `REFLECT_IDLE_MAX_PER_SWEEP` | `5` | Max sessions enqueued per sweep. |
| `REFLECT_IDLE_PROJECTS_ROOT` | `~/.claude/projects` | Transcript root scanned. |
| `REFLECT_IDLE_LOG_MAX_BYTES` | `1048576` | `idle.log` rotation threshold. |
| `REFLECT_WATCH_DRAIN_STALE_SEC` | `3600` | Warn when the queue is non-empty and the last drain start is older than this. |
| `REFLECT_WATCH_DRAIN_RUNNING_SEC` | `900` | Warn when a live drain process has been running longer than this. |
| `REFLECT_WATCH_INGEST_STALE_DAYS` | `7` | Warn when the ingest log has not been touched for this many days. |
| `REFLECT_WATCH_DRAIN_LABEL` | `com.reflect.drain` | launchd label checked for the drain job. |
| `REFLECT_WATCH_MAINTENANCE_LABEL` | `com.reflect.maintenance` | launchd label checked for the watchdog itself. |
| `REFLECT_WATCH_SKIP_LAUNCHD` | `0` | `1` skips launchd label checks (they are always skipped off macOS). |
| `REFLECT_WATCH_DISABLE_INGEST` | `0` | `1` skips the ingest-staleness check. |
| `REFLECT_WATCH_FORCE_JSON_ERRORS` | `0` | `1` writes `errors.json` directly instead of through `reflect errors append`. |

`REFLECT_IDLE_TIMEOUT` is unrelated to the idle sweep; it belongs to the model daemon below.

### Engine and models

Read by the `reflect` CLI (`reflect-kb`).

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_EMBED_MODEL` | `all-mpnet-base-v2` | Embedding model for indexing and `reflect embed`. Changing it needs a fresh reindex, because similarity must stay in one vector space. |
| `REFLECT_CE_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder rerank model (about 90 MB, downloaded on first use). |
| `REFLECT_NO_DAEMON` | unset | Exactly `1`: skip the model daemon and load models in-process every call. |
| `REFLECT_IDLE_TIMEOUT` | `1800` | Seconds the model daemon idles before exiting (`0` means never). |
| `REFLECT_DAEMON_TIMEOUT` | `120` | Client per-request timeout to the daemon, seconds (covers a cold model load). |
| `TMPDIR` | `/tmp` | Where the daemon socket and locks go; falls back to `/tmp` if the path would exceed the Unix socket limit. |
| `HF_HOME`, `SENTENCE_TRANSFORMERS_HOME` | `$REFLECT_STATE_DIR/models` | Set via `setdefault` before loading the cross-encoder; your own value wins. |
| `HF_HUB_DISABLE_TELEMETRY` | `1` (set if unset) | Set by the cross-encoder loader. |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `1` (set if unset) | Set by the two recall hooks so a cached model never hits the network. Export `0` to allow downloads. |
| `REFLECT_PG_DSN` | unset | Postgres DSN. With `REFLECT_WORKSPACE_ID`, moves the graph, vectors and community reports to shared Postgres instead of local files. The generic `DATABASE_URL` is deliberately not a trigger. |
| `REFLECT_WORKSPACE_ID` | unset | Tenant scope for the Postgres backend. Required together with the DSN. |
| `DATABASE_URL` | unset | Fallback DSN inside the Postgres nano-graphrag connection helper when no `pg_dsn` is passed. |
| `CLAUDECODE`, `CODEX_CLI`, `GITHUB_COPILOT` | unset | Presence selects the harness label (`claude`, `codex`, `copilot`, else `other`) recorded on metrics lines. |
| `CLAUDE_PLUGIN_ROOT` | unset (set by Claude Code) | `reflect timeline` uses it to find `scripts/reflect_timeline.sh`. |

### Lifecycle events and git hook

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_EVENTS_ON_<EVENT>` | unset | Shell command for an event, for example `REFLECT_EVENTS_ON_LEARNING_CREATED`. Beats `[events.on]`. |
| `REFLECT_EVENT`, `REFLECT_EVENT_PAYLOAD` | set by reflect | Exported **to** event hook commands: the event name and its JSON payload. Not inputs. |
| `REFLECT_SESSION_ID` | unset | Session id fallback for `post_commit.sh` (after `CLAUDE_SESSION_ID`) and `output_generator.py`. |
| `CLAUDE_SESSION_ID` | unset (set by the harness) | Session anchor for recall follow-up tracking and the git post-commit link. |
| `REFLECT_PYTHON` | `python3` | Interpreter `post_commit.sh` uses. |
| `REFLECT_SCRIPTS_DIR` | derived from the hook location | Overrides where `post_commit.sh` finds `reflect_db.py`; the installer pins it to an absolute path. |

### Status-line timeline

Read by `plugin/scripts/reflect_timeline.sh`.

| Variable | Default | Effect |
|---|---|---|
| `REFLECT_TIMELINE_DISABLE` | `0` | `1` renders nothing. |
| `REFLECT_TIMELINE_TOKEN_FULLBAR` | `20000` | Token count that fills a bar. |
| `REFLECT_TIMELINE_SCALE_TOK` | `20000` | Total-token sparkline scale. |
| `REFLECT_TIMELINE_SCALE_UNC` | `15000` | Uncached-token scale. |
| `REFLECT_TIMELINE_SCALE_CHR` | `100000` | Cache-read scale. |
| `REFLECT_TIMELINE_SCALE_OUT` | `5000` | Output-token scale. |
| `REFLECT_TIMELINE_PROJECT_DIR` | unset | Project dir fallback when the git-root walk fails (non-git contexts). |
| `REFLECT_TIMELINE_SESSION_ID` | unset | Pin the session JSONL to read. |

### Hermes

| Variable | Default | Effect |
|---|---|---|
| `FLEET_MEMORY_BACKEND` | `shadow` | `bank`, `shadow` or `reflect`. See [Hermes shims](/ainb-reflect-memory/reference/hooks/#hermes-shims). |
| `REFLECT_FLEET_TIMEOUT` | `10` | Seconds allowed for the shim's recall subprocess. |
| `REFLECT_RECALL_SCRIPT` | auto-located | Path to a deployed `recall.py` (if it does not exist, no recall runs). |
| `REFLECT_RECALL_RUNNER` | `uv run --script` | Command prefix used to execute `recall.py`. For tests. |

## Known gaps

Documented so you do not tune a dead knob:

- Reserved `reflect.toml` keys: `discovery.staleness_days`, `indexers.graphrag.*`, `policies.*` (all three), `telemetry.*`, `providers.hermes.home_dir`, every `recall.*` key. Their overlay env vars also have no effect.
- `hermes` in `discovery.enabled_providers` is accepted and skipped.
- A malformed numeric value in `REFLECT_RECALL_MIN_OVERLAP`, `REFLECT_RECALL_MAX_TOKENS` or `REFLECT_SUBAGENT_RECALL_TIMEOUT` raises at import, before the hook's silent-fail wrapper. See the caution on the [Hooks reference](/ainb-reflect-memory/reference/hooks/#exit-code-and-output-contract).
- `REFLECT_DISABLED` gates only the drain family. To stop hooks, disable the plugin.
