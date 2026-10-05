---
title: Fleet and Hermes import
description: Import fleet-lambda patterns, discoveries and corrections into reflect as quarantined learnings, and wire the Hermes shims for shadow recall and capture.
sidebar:
  order: 30
---

`reflect fleet` brings memory from a fleet-lambda (Hermes) deployment into the reflect knowledge base. Imported notes are typed, deduplicated by content, counted, and **quarantined**: they stay out of normal Claude Code and Codex recall until something explicitly lifts the quarantine. A second piece, the Hermes adapter, lets a fleet-lambda hook call reflect for recall and capture without reflect owning the hook wiring.

```
fleet-lambda artifacts                reflect
──────────────────────                ───────
patterns.jsonl          ┐
discoveries(-archive)   ├─ reflect fleet ingest ─▶ ~/.learnings/documents/*.md
corrections.md / jsonl  ┘        │                    (quarantine: true)
                                 ├─▶ ~/.reflect/fleet-ledger.json (counts)
                                 └─▶ one reindex

Hermes hook ─▶ pre_llm_recall.py ─▶ recall.py --format fleet-context
Hermes hook ─▶ post_llm_capture.py ─▶ ~/.reflect/pending_reflections.jsonl
```

## Import: `reflect fleet ingest`

```bash
reflect fleet ingest --root ~/.clan/learnings --dry-run   # parse and classify, write nothing
reflect fleet ingest --root ~/.clan/learnings             # import, then one reindex
reflect fleet ingest --root ./fleet-export --kinds patterns,corrections --no-reindex
reflect fleet status                                       # ledger stats
```

| Flag | Meaning |
|---|---|
| `--root PATH` | Required. Directory holding the fleet-lambda artifacts. Must exist. |
| `--kinds` | Comma list from `patterns`, `discoveries`, `corrections`. Default: all three. Unknown values error out. |
| `--dry-run` | Parse and classify only. No files, no ledger update, no reindex. |
| `--no-reindex` | Write files but skip the post-import graph reindex (batching, tests). |

The command prints a table of `imported`, `deduped`, `skipped` and `errors`, then each skipped or errored source. It exits 1 when `errors` is non-zero. It never prompts, so it is safe in non-interactive pipelines.

### What it reads

Files are looked up directly under `--root`:

| Kind | Files | Notes |
|---|---|---|
| `patterns` | `patterns.jsonl` | One note per line. Title from `title`, `name` or `pattern`; description, rationale, example and context become body sections. |
| `discoveries` | `discoveries.jsonl`, `discoveries-archive.jsonl` | Problem, context and solution become body sections. Archive entries get `workflow_state: archived`. Entries with `retracted: true` are skipped. |
| `corrections` | `corrections.md`, `pending-corrections.jsonl` | The markdown ledger is split on `#` headings, one note per section. A file without headings becomes one note. |

Malformed JSON lines and non-object lines are counted as skipped, with the file and line number reported; one bad line never aborts the run.

### What it writes

Each entry becomes a Markdown note in the KB `documents/` directory (default `~/.learnings`, override with `$GLOBAL_LEARNINGS_PATH`), named `<slug>-<hash6>.md`. That is the same content-addressed id `reflect add` uses, so a re-import maps to the same file. An entity sidecar (`<id>.entities.yaml`) is generated best-effort.

Frontmatter on every imported note:

| Field | Value |
|---|---|
| `name`, `title`, `key_insight`, `tags` | From the source entry. `key_insight` falls back to the first line of the solution or body, then the title. |
| `category` | `fleet-pattern`, `fleet-discovery` or `fleet-correction` |
| `source_system` | `fleet` |
| `source_kind` | `patterns`, `discoveries` or `corrections` |
| `source_path` | The file the entry came from |
| `content_hash` | sha256 of title plus body (the dedupe key) |
| `authority` | `advisory` (importer never sets a higher tier) |
| `quarantine` | `true` |
| `domain` | Inferred from tags and agent name (see below); default `coding` |
| `workflow_state` | `open`, or `archived` for archived discoveries |
| `occurrences` | Times this content has been seen |
| `supersedes` | Optional, copied from the source entry |

Domain inference is a substring match over tags and the agent name, first hit wins: `personal`, `research`, `devops`/`infra`/`ops` (all mapped to `ops`), `security`, `writing`. Anything else is `coding`.

### Dedupe and the ledger

Re-importing the same content does not create a new file. The importer increments a counter in `~/.reflect/fleet-ledger.json` (path follows `$REFLECT_STATE_DIR`), rewrites `occurrences` on the existing note, and reports it as `deduped`.

- The ledger maps `content_hash` to `{doc_id, count, first_seen, last_seen}`.
- Updates are serialised with a file lock and written atomically (temp file plus replace), so parallel ingests cannot lose increments or leave a torn file.
- When a hash reaches a count of 3, a `fleet_promotion_candidate` metric is written once to `~/.learnings/metrics.jsonl`. reflect never promotes anything itself; that decision belongs to Fleet.
- `reflect fleet status` prints documents, total occurrences and promotion candidates, plus the ledger path.

The importer writes all files first and then runs **one** full reindex. Per-document incremental indexing fragments the graph communities, so do not script `reflect add` in a loop for bulk loads.

## Quarantine and recall

Quarantined notes are filtered out of `recall.py` results by default. Two flags change that:

| Flag | Effect |
|---|---|
| `--include-quarantined` / `--no-include-quarantined` | Explicitly include or exclude quarantined notes. |
| `--format fleet-context` | Fleet-shaped output. Implies `--include-quarantined` unless you pass `--no-include-quarantined`. |

Two soft ranking terms apply to fleet notes. Both are neutral (exactly 1.0) when absent, so legacy notes rank as before:

| Term | Trigger | Strength | Tune or disable |
|---|---|---|---|
| Domain affinity | `--domain-hint X` equals a note's `domain` | Default alpha 0.2 (+10% for a match) | `RECALL_DOMAIN_ALPHA`, `recall.boost.domain_affinity_alpha`, `0` disables |
| Authority tier | note `authority` | Default alpha 0.1 (law and promoted high, advisory neutral, archived low) | `RECALL_AUTHORITY_ALPHA`, `recall.boost.authority_alpha`, `0` disables |

Neither is a hard filter: a strong coding note still surfaces on a personal query.

### The `fleet-context/v1` block

`--format fleet-context` renders an authority-labelled block that a fleet-lambda hook can inject ahead of an LLM turn. Limits match fleet-lambda's BANK: at most 5 items and 2000 estimated tokens for the whole block (lowest-ranked items are dropped until it fits). Sections run Law, Promoted memory, Advisory memory, Archived; a note with no or unknown authority lands in Advisory. Each item shows title, key insight, `source:` path and score. The header carries the marker `<!-- fleet-context/v1 -->` so a consumer can refuse an unknown version.

```bash
recall.py --format fleet-context --domain-hint personal --limit 5 -- "weekly planning"
```

## Hermes adapter

Hermes has no plugin runtime of its own, and fleet-lambda owns hook registration, so the adapter only **deploys files**: the reflect skills, `reflect.toml`, and two shim scripts into `~/.hermes/skills/`. It writes no `hooks.json`.

```bash
python plugin/adapters/hermes/hermes_adapter.py install --dry-run   # show the plan
python plugin/adapters/hermes/hermes_adapter.py install
python plugin/adapters/hermes/hermes_adapter.py install --force     # overwrite hand-written siblings
python plugin/adapters/hermes/hermes_adapter.py uninstall
```

Installed `SKILL.md` files carry a `managed_by: reflect-kb/adapters/hermes` marker so uninstall leaves hand-written siblings alone. For the per-harness install steps see [Install on Hermes](/ainb-reflect-memory/install/hermes/).

### `pre_llm_recall.py`

A fleet-lambda hook pipes `{"prompt", "agent_id", "domain_hint", "session_id"}` as JSON on stdin. The mode comes from `FLEET_MEMORY_BACKEND`:

| Value | Behaviour |
|---|---|
| `bank` | Exit immediately, no output. Fleet's own bank owns recall. |
| `shadow` (default) | Run recall, log telemetry, print **nothing**. Measurement mode for a rollout. |
| `reflect` | Run recall and print the `fleet-context/v1` block to stdout. |

Recall is a subprocess call to the deployed `recall.py` with `--format fleet-context --include-quarantined --limit 5 --no-followup --no-gap-log`, plus `--domain-hint` when given. The wall-clock limit is `REFLECT_FLEET_TIMEOUT` seconds (default 10). Every run in `shadow` or `reflect` mode appends an `op: "fleet_shadow_recall"` event to `~/.learnings/metrics.jsonl` with `hits`, `tokens_est`, `latency_ms`, `agent` and `mode`; `reflect metrics stats` aggregates these into event count, average hits, average and p95 latency, and average tokens.

### `post_llm_capture.py`

A hook pipes `{"last_user_msg", "last_assistant_msg", "transcript_tail", "session_id", "agent_id"}` after a turn. The shim appends the turn to `~/.reflect/hermes-transcripts/<session>.jsonl` and enqueues one entry per pending session on `~/.reflect/pending_reflections.jsonl`, the same queue the Claude Code `Stop` hook feeds, so the background [drain](/ainb-reflect-memory/concepts/drain/) processes it later. The entry carries `source: "hermes"` and `agent_id`. If the user message contains a correction word (`no`, `wrong`, `actually`, `stop`, `don't`, `should be`) the entry is tagged `priority: "high"`; classification itself still happens in `/reflect`.

### Failure behaviour

Both shims always exit 0. Any exception becomes a silent exit plus a scrubbed breadcrumb at `~/.reflect/last-event.json` (credential-shaped strings are masked), so a reflect fault never blocks or pollutes a Hermes turn.

## Limits

- The importer handles patterns, discoveries and corrections only. Journals, HOT memory snapshots and skill docs are not imported.
- Nothing lifts quarantine yet. There is no `unquarantine` command; promotion is a Fleet-side decision.
- The shims do not rewrite `${CLAUDE_PLUGIN_ROOT}` anchors inside the copied `SKILL.md` files, unlike the Codex adapter.
- Hook wiring and the `FLEET_MEMORY_BACKEND` switch live in the fleet-lambda repo, not here.

Design background: [Fleet/Hermes adapter plan](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/docs/design/fleet-hermes-adapter.md) and [spec](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/docs/design/fleet-hermes-adapter-spec.md). Related: [CLI reference](/ainb-reflect-memory/reference/cli/), [Configuration](/ainb-reflect-memory/reference/configuration/), [KB format](/ainb-reflect-memory/reference/kb-format/).
