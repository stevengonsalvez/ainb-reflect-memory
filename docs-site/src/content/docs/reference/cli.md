---
title: CLI reference
description: Every reflect command and subcommand with flags, defaults, examples, output streams and exit codes, verified against reflect-kb 0.3.0.
sidebar:
  order: 2
---

`reflect` is the command-line face of the knowledge base. It is installed by the Python package `reflect-kb` and covers the capture, index and recall loop: `reflect add` writes a note, `reflect reindex` rebuilds the graph index, `reflect search` reads it back. Everything below was checked against `src/reflect_kb/cli/` and by running `--help` and the commands on a throwaway KB.

```text
reflect [--version] [--help] COMMAND [ARGS]...
```

There is exactly one console script, `reflect` (`reflect = "reflect_kb.cli.main:main"` in `pyproject.toml`). `reflect-kb` is the package name, not a binary. `reflect --version` prints the CLI version (`reflect, version 0.3.0`), which is independent of the plugin version.

## Command map

| Group | Commands | Needs `[graph]` extra |
|---|---|---|
| [Write](#write-commands) | `init`, `add`, `generate-sidecars`, `reindex` | `reindex` only |
| [Read](#read-commands) | `search`, `stats`, `critical-patterns` | `search` only |
| [Recall helpers](#recall-helpers) | `rerank`, `embed` | `embed` (hard), `rerank` (degrades) |
| [Observe](#observe-commands) | `metrics stats`, `errors count/ack/append`, `timeline` | no |
| [Integrations](#integration-commands) | `serve`, `fleet ingest/status`, `issues run/ledger/queue` | no |

:::note[Slim build vs `[graph]`]
The base install is slim: click, rich, pyyaml, httpx. The GraphRAG and vector stack (`sentence-transformers`, `nano-graphrag`, `networkx`, and friends) lives in the `[graph]` extra. On a slim install, observed behaviour:

- `reflect add` still saves the note and sidecar, prints `Warning: Graph indexing failed: No module named 'networkx'`, and exits 0.
- `reflect search` prints the error (JSON: `{"error": ..., "results": []}`) and exits 0.
- `reflect reindex` and `reflect embed` crash with a `ModuleNotFoundError` traceback and exit 1.
- `reflect rerank` degrades cleanly: `{"available": false, ...}`, exit 0.

Install with `uv tool install 'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'`. See [Claude Code install](/ainb-reflect-memory/install/claude-code/).
:::

## Global behaviour

| Topic | Behaviour |
|---|---|
| KB root | `$GLOBAL_LEARNINGS_PATH`, else `~/.learnings`. Holds `documents/`, `nano_graphrag_cache/`, optional `archived/`. |
| Auto-create | Any invocation that reaches a subcommand (including `<command> --help`) creates `documents/` and `nano_graphrag_cache/` under the KB root. Top-level `reflect --help` and `--version` do not. |
| Human output | Rich output (tables, panels, status lines) is written to **stderr** for every group except `metrics`. Machine output (`--format json`, `simple`, `rerank`, `embed`, `errors`) goes to stdout via plain echo. |
| State dir | `$REFLECT_STATE_DIR`, else `~/.reflect`. Used by `errors`, `fleet`, `issues`, `rerank` model cache. |
| Metrics log | Every `search`, `rerank`, `embed` call appends one line to `~/.learnings/metrics.jsonl` (best effort, rotated at 10 MB to `metrics-<ts>.jsonl.bak`). This path is fixed to the home directory and ignores `$GLOBAL_LEARNINGS_PATH`. |
| Stale index | `search` warns on stderr when `documents/` is newer than the graph cache: run `reflect reindex`. |

### Exit codes

Most commands report soft failures in text and still exit 0. Do not rely on the exit code alone for `add` and `search`; parse output.

| Code | When |
|---|---|
| `0` | Success, and also several soft failures: `add` with missing frontmatter, `search` engine error, empty KB. |
| `1` | Uncaught exception (for example `reindex` or `embed` without `[graph]`), `click.Abort` (for example `timeline --explain` with no plugin), `fleet ingest` with `errors > 0`, or a non-zero return from the timeline helper. |
| `2` | Click usage errors (unknown command, missing argument, bad path or choice), and `add` when the target note exists, stdin is not a TTY and `--force` was not given. |

## Write commands

### `reflect init`

Create the KB layout and a git repo at the KB root.

```bash
reflect init
```

Creates `documents/` and `nano_graphrag_cache/`. If the root is not already a git repo it runs `git init`, writes a `.gitignore` (`.venv/`, `__pycache__/`, `*.pyc`, `nano_graphrag_cache/`) if missing, and makes an initial commit. Idempotent. No flags. Default root is `~/.learnings` (older docs said `~/.claude/global-learnings`; that is wrong for 0.3.0).

### `reflect add FILE_PATH`

Add one note. The file needs YAML frontmatter with at least `title`, `category`, `key_insight`.

| Flag | Default | Effect |
|---|---|---|
| `-e, --entities PATH` | none | Use this `.entities.yaml` sidecar instead of auto-extracting. Copied next to the note. |
| `-f, --force` | off | Overwrite an existing note with the same generated id without prompting. |

```bash
reflect add ./my-solution.md
reflect add ./my-solution.md --entities ./my-solution.entities.yaml
reflect add --force ./my-solution.md      # required in scripts and CI
```

Behaviour:

- Destination is `documents/<slug>-<hash6>.md`. The id is `slug(title)[:50]` plus the first 6 hex chars of `sha256(title + "\n" + body)`. Same title and same body gives the same id (idempotent re-add); same title with a different body gives a different file.
- If the id exists: with `--force` it overwrites; on a TTY it asks; with no TTY it exits **2** with an instruction to use `--force`.
- Without `--entities`, a heuristic sidecar (no LLM) is written when at least one entity is found.
- Then it inserts into the graph (needs `[graph]`; failure is a warning and the note is still saved), then runs `qmd update` and `qmd embed` if a `qmd` binary is on `PATH` (30 s and 120 s timeouts).
- Missing frontmatter or required fields: prints an error and exits **0** without writing.

### `reflect generate-sidecars`

Backfill `.entities.yaml` sidecars heuristically (no LLM) for notes that lack one.

| Flag | Default | Effect |
|---|---|---|
| `--force` | off | Regenerate every sidecar, replacing existing ones. |

Prints per-note counts, then `Generated / Skipped / Failed`. Follow with `reflect reindex --force`.

### `reflect reindex`

Rebuild the GraphRAG index from all notes in a single batch (per-note incremental inserts fragment graph communities).

| Flag | Default | Effect |
|---|---|---|
| `--force` | off | Clear `nano_graphrag_cache/` first and rebuild from scratch. |

Auto-generates missing sidecars first, logs sidecar parse problems to the error sink (`errors.json`, source `parse`), then indexes. Prints `No documents to index.` and exits 0 on an empty KB. Run it after `reflect serve` curation or manual edits. Requires `[graph]`.

## Read commands

### `reflect search QUERY`

Search the index and return raw retrieved context (no LLM synthesis).

| Flag | Default | Effect |
|---|---|---|
| `-m, --mode` | `naive` | `naive`: vector similarity. `local`: entity neighbourhood via the graph. `global`: community reports. |
| `-t, --tags TEXT` | none | Comma-separated; appended to the query text as `tags: ...` (a soft hint, not a filter). |
| `-c, --category TEXT` | none | Appended to the query as `category: ...` (soft hint). |
| `-l, --limit INT` | `10` | Accepted but **not applied** in 0.3.0: the value is never passed to the engine. |
| `-f, --format` | `rich` | `rich` (panel on stderr), `json`, `simple` (bare context on stdout). |

```bash
reflect search "tokio runtime panic"
reflect search "async timeout" --mode local
reflect search "n+1 query" --tags rust,performance --format json
```

JSON shapes (stdout):

```json
{"query": "flyway seed", "mode": "naive", "context": "...retrieved context..."}
```

```json
{"query": "flyway seed", "mode": "naive", "results": [], "message": "No results found"}
```

```json
{"query": "flyway seed", "mode": "naive", "error": "No module named 'networkx'", "results": []}
```

Always exits 0, including on error. The stale-index warning goes to stderr so JSON on stdout stays clean.

### `reflect stats`

Print KB statistics: total documents, repository path, graph entity and relationship counts (or `Not initialized`), documents with sidecars, then breakdown tables by category and by confidence. No flags.

### `reflect critical-patterns`

List high-confidence, widely applicable notes: `confidence == high` and `category` in `architecture-decisions` or `patterns`.

| Flag | Default | Effect |
|---|---|---|
| `-l, --language TEXT` | none | Match frontmatter `language` exactly, or a tag (case-insensitive). |
| `-d, --domain TEXT` | none | Match a tag, or a substring of the body (case-insensitive). |

```bash
reflect critical-patterns --language rust
```

## Recall helpers

Stdin/stdout JSON tools used by the recall pipeline (see [recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/)). Both read `{"candidates": [{"id": "...", "text": "..."}]}` from stdin. An invalid payload prints `{"available": false, "error": "invalid payload"}` and exits 0. Both log a metric and prefer the persistent model daemon (see [environment](#environment-variables)).

### `reflect rerank QUERY`

Score candidates with a local cross-encoder. Raw logits, higher is more relevant.

| Flag | Default | Effect |
|---|---|---|
| `--batch-size INT` | `20` | Prediction batch size. |
| `--model TEXT` | `ms-marco-MiniLM-L-6-v2` | Override the cross-encoder (default also settable via `REFLECT_CE_MODEL`). |

```bash
echo '{"candidates":[{"id":"a","text":"flyway runs before seed"}]}' | reflect rerank "migration order"
# {"available": true, "model": "...", "scores": {"a": 3.21}}
```

The model downloads on first use into `~/.reflect/models/` (honours `$REFLECT_STATE_DIR`). Without `sentence-transformers`: `{"available": false, "error": "sentence-transformers not installed (slim build)"}`, exit 0. Any model failure degrades the same way.

### `reflect embed QUERY`

Embed the query and candidate texts with the index embedding model (`all-mpnet-base-v2`, unit-normalised, 6 decimal places). No flags.

```bash
echo '{"candidates":[{"id":"a","text":"flyway runs before seed"}]}' | reflect embed "migration order"
# {"available": true, "model": "all-mpnet-base-v2", "query_embedding": [...], "embeddings": {"a": [...]}}
```

Requires `[graph]`. Note the module import happens before the degrade logic, so on a slim install this crashes with exit 1 rather than emitting `{"available": false}`. Model failures after import do degrade.

## Observe commands

### `reflect metrics stats`

Aggregate the recall-metrics JSONL log into a last-N-days window and an all-time window.

| Flag | Default | Effect |
|---|---|---|
| `--metrics-path PATH` | `~/.learnings/metrics.jsonl` | Read a different file. |
| `--format` | `table` | `table` or `json`. |
| `--window-days INT` | `7` | Size of the recent window (label `last-<N>d`). |

Per window: total events, recall events, recall with hits, hit rate, p50 and p95 latency (ms), top tags, and (when present) fleet shadow-recall averages. `--format json` emits the full report (`all_time`, `last_7d`, `generated_at`, `metrics_path`) on stdout. This is the only group that prints tables to stdout.

### `reflect errors`

Triage the error sink at `$REFLECT_STATE_DIR/errors.json` (default `~/.reflect/errors.json`; bounded to 200 records, 24 h dedupe window, file-locked). Exposed on the installed binary so the statusline badge, drain hook and `/reflect:errors-ack` skill avoid a bare `python3 -m reflect_kb.errors` (which only works when `reflect_kb` is importable by the system Python). The module entrypoint still works.

| Command | Arguments | Output |
|---|---|---|
| `reflect errors count` | none | Number of un-acked errors (drives the statusline badge). |
| `reflect errors ack [IDS...]` | error ids; none means all | Number acked. |
| `reflect errors append` | see below | The (deduped) error id, `err-<6 hex>`. |

`append` flags:

| Flag | Default | Notes |
|---|---|---|
| `--severity` | `error` | `error`, `warn`, `info`. |
| `--source` | required | Origin, for example `parse`. |
| `--kind` | required | Short machine tag. |
| `--message` | required | Truncated to 500 chars. |
| `--context` | `{}` | JSON object string; invalid JSON becomes `{}`. |

```bash
reflect errors append --source drain --kind timeout --message "claude -p exceeded 180s" --context '{"entry":"abc"}'
# err-b5f96c
reflect errors count        # 1
reflect errors ack          # 1
```

Re-appending the same `source|kind|message` within 24 h bumps `count` and un-acks the record instead of adding a row.

### `reflect timeline`

Show or drill into the statusline dashboard.

| Flag | Default | Effect |
|---|---|---|
| `--explain ROW` | none | Drill-down for one row: `REC`, `MEM`, `ING`, `DRN`, `TOK`, `ERR`, `COM`, `AGT`, or `all`. |

With no flag it prints a usage hint and exits 0. With `--explain` it shells out to the plugin's `reflect_timeline.sh`, found via `$CLAUDE_PLUGIN_ROOT/scripts/`, else the newest match under `~/.claude/plugins/cache/*/reflect/*/plugin/scripts/` (or the older `.../scripts/`). If none is found it prints `error: reflect plugin not found.` and exits 1; otherwise it exits with the helper's code.

## Integration commands

### `reflect serve`

Launch the local memory browser. Full endpoint contract: [serve API](/ainb-reflect-memory/reference/serve-api/). Walkthrough: [memory browser guide](/ainb-reflect-memory/guides/memory-browser/).

| Flag | Default | Effect |
|---|---|---|
| `--host TEXT` | `127.0.0.1` | Bind address. Not restricted in code; there is no auth, so keep it on loopback. |
| `-p, --port INT` | `8377` | Listen port. |
| `--repo DIRECTORY` | `$GLOBAL_LEARNINGS_PATH` or `~/.learnings` | KB to browse; must already exist. |

```bash
reflect serve                       # http://127.0.0.1:8377
reflect serve --port 8942 --repo /path/to/kb
```

Runs in the foreground until interrupted. Run it in a terminal multiplexer if you want it backgrounded. Curation edits metadata only; run `reflect reindex` afterwards.

### `reflect fleet`

Import fleet-lambda memory as quarantined learnings. Imported notes carry `quarantine: true` and `authority: advisory`, so they stay out of claude and codex recall scope until promoted.

**`reflect fleet ingest --root PATH`**

| Flag | Default | Effect |
|---|---|---|
| `--root PATH` | required, must be an existing directory | Directory with the fleet-lambda artifacts. |
| `--kinds TEXT` | `patterns,discoveries,corrections` | Comma-separated subset. Unknown values exit 2. |
| `--dry-run` | off | Parse and classify; write no files, ledger or index. |
| `--no-reindex` | off | Skip the single post-import `reindex`. |

Files read under `--root`: `patterns.jsonl`; `discoveries.jsonl` and `discoveries-archive.jsonl` (entries with `retracted: true` are skipped); `corrections.md` and `pending-corrections.jsonl`. Prints an `imported / deduped / skipped / errors` table to stderr, then (unless dry-run or `--no-reindex`, and only if something was imported) runs one `reindex`. Dedupe is by content hash through the ledger, so re-importing bumps an occurrence count instead of erroring. Exits 1 if `errors > 0`.

**`reflect fleet status`** prints ledger counts: documents, occurrences, promotion candidates (entries seen at least 3 times), and the ledger path (`<state dir>/fleet-ledger.json`).

```bash
reflect fleet ingest --root ./fleet-memory --dry-run
reflect fleet ingest --root ./fleet-memory
reflect fleet status
```

### `reflect issues`

Turn recent session transcripts into privacy-sanitised GitHub issues. Pipeline: queue, distill (about 30x, no LLM), analyse (one bounded `claude -p` call, skipped with reason `claude-unavailable` if `claude` is not on `PATH`), sanitise every candidate, dedupe (in-batch, local ledger, `gh issue list`), file with `gh issue create`. Always preview with `--dry-run` first.

**`reflect issues run`**

| Flag | Default | Effect |
|---|---|---|
| `--dry-run` | off | Print the exact bodies that would be filed; never calls `gh issue create`. |
| `--repo OWNER/NAME` | `[issues].repo`, else the cwd repo `gh` resolves | Target repository. |
| `--limit INT` | `[issues].limit`, else `20` | Max recent transcripts to pull from the queue. |
| `--model TEXT` | `[issues].model`, else `sonnet` | Model for the analyser. |
| `--map KEY=VALUE` | none, repeatable | Extra sanitiser substitution, for example `--map AcmeCorp=<company>`. |
| `--label TEXT` | `[issues].label`, else `reflect` | Provenance label on every issue (auto-created). Empty string disables. |
| `--title-prefix TEXT` | `[issues].title_prefix`, else `reflect: ` | Title prefix. Empty string disables. |
| `-f, --format` | `rich` | `rich` or `json`. |

Resolution order for repo, limit, model, label and title prefix: explicit flag, then the `[issues]` table of `reflect.toml`, then the built-in default. `reflect.toml` is found via `$REFLECT_CONFIG`, then `$REFLECT_STATE_DIR/reflect.toml`, then `~/.reflect/reflect.toml`, then the bundled `plugin/reflect.toml` of a source checkout. A missing or malformed file yields `{}`.

`--format json` keys: `dry_run`, `transcripts_seen`, `transcripts_distilled`, `analyze_reason`, `candidates`, `filed[]` (`title`, `fingerprint`, `gh_issue_number`, `gh_url`), `skipped[]` (`title`, `reason`, `existing`), `previews`, `audit` (residual-suspicious findings for human review), `notes`.

**`reflect issues ledger [-f rich|json]`** shows issues already filed (idempotency ledger at `<state dir>/filed_issues.json`).

**`reflect issues queue [--limit N]`** (default 20) lists the transcripts `run` would analyse, from `<state dir>/pending_reflections.jsonl`.

```bash
reflect issues queue
reflect issues run --dry-run
reflect issues run --repo myorg/myrepo --limit 10 --map 'AcmeCorp=<company>'
```

## Environment variables

Read by the CLI itself. Drain and hook variables (`REFLECT_DRAIN_*`, `REFLECT_DISABLED`, and so on) are documented in [configuration](/ainb-reflect-memory/reference/configuration/).

| Variable | Default | Used by |
|---|---|---|
| `GLOBAL_LEARNINGS_PATH` | `~/.learnings` | KB root for every command and `serve`. |
| `REFLECT_STATE_DIR` | `~/.reflect` | `errors`, `fleet`, `issues`, rerank model cache, `reflect.toml` lookup. |
| `REFLECT_CONFIG` | unset | Explicit `reflect.toml` path. |
| `REFLECT_EMBED_MODEL` | `all-mpnet-base-v2` | Embedding model for indexing and `embed`. Changing it requires `reflect reindex --force`. |
| `REFLECT_CE_MODEL` | `ms-marco-MiniLM-L-6-v2` | Default cross-encoder for `rerank`. |
| `REFLECT_NO_DAEMON` | unset | `1` skips the persistent model daemon and always loads models in-process. |
| `REFLECT_IDLE_TIMEOUT` | `1800` | Daemon idle seconds before exit (`0` means never). |
| `REFLECT_DAEMON_TIMEOUT` | `120` | Client per-request seconds to the daemon. |
| `REFLECT_PG_DSN`, `REFLECT_WORKSPACE_ID` | unset | Opt-in shared Postgres backend for the graph store. Both must be set. |
| `CLAUDE_PLUGIN_ROOT` | set by Claude Code | Locates `reflect_timeline.sh` for `timeline --explain`. |

The model daemon is a unix-socket process that keeps torch and the models warm so parallel sessions do not each load about 3.5 GB. It is spawned on demand by the embed and rerank paths (`embed`, `rerank`) and is a pure optimisation: any failure falls back to in-process loading.

## Not `reflect` subcommands

Some things older docs listed as CLI commands live in the plugin instead.

| Thing | Where it actually lives |
|---|---|
| `reflect cost` | Not a subcommand in 0.3.0 (`reflect cost` is "No such command"). Use the `/reflect:cost` skill, which runs `plugin/scripts/reflect_cost.py`. |
| `python -m reflect_kb.errors` | Legacy entrypoint, same `append`, `count`, `ack` as `reflect errors`. |

`reflect_cost.py` flags: `--since` (default `30d`; also `7d`, `24h`), `--by` (`day`, `transcript`, `model`, `outcome`, `writer`; default `day`), `--top` (default 15), `--outlier-tokens` (default 5000000), `--json`, `--state-dir`, `--followup` (recall followup rate from the metrics log), `--metrics-path` (or `$REFLECT_METRICS_PATH`), `--quota` (subscription-quota windows and writer-gate state). It reads the drain cost log at `~/.reflect/drain-cost.jsonl`.

## Quick start

```bash
reflect init
reflect add ./my-solution.md
reflect reindex                     # needs [graph]
reflect search "how did we fix the tokio runtime panic"
reflect stats
reflect serve                       # browse and curate in a local UI
```
