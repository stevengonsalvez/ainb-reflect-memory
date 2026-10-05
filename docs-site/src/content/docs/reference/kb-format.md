---
title: KB on-disk format
description: Directory layout, learning note frontmatter, entity sidecars, ids, and archive and supersession semantics of the reflect knowledge base.
sidebar:
  order: 4
---

The KB is plain files. One markdown note per learning, an optional `.entities.yaml` sidecar beside it, and derived indexes next to them. Ranking state lives in SQLite, never in the notes. Everything on this page is read from the code on `main`; the [OKF profile](/ainb-reflect-memory/reference/okf-profile/) (not merged) is documented separately.

## Layout

```
~/.learnings/                         KB root (git repo after `reflect init`)
├── documents/
│   ├── <doc-id>.md                   learning note (frontmatter + body)
│   ├── <doc-id>.entities.yaml        entity sidecar (optional, same stem)
│   └── memories/<project>/           archived raw originals from /reflect:ingest
├── archived/                         soft-archived notes (reflect serve)
├── review-queue/<slug>.yaml          LOW-confidence pointers (never indexed)
├── nano_graphrag_cache/              derived graph + vector index (gitignored)
├── shards/<project>/                 per-project KB: documents/ + nano_graphrag_cache/
│   └── branches/<branch>/            per-branch sub-shard (`/` becomes `__`)
└── .memory-ingest-log.yaml           content hashes already ingested

~/.reflect/
├── reflect.db                        SQLite: ledger, signals, history (not in git)
└── episodes/ep-<date>-<time>.md      session episode notes
```

| Location | Written by | Notes |
|---|---|---|
| `documents/*.md` | `reflect add`, drain, hooks, fleet import | The corpus. `reflect add` copies a note here under a generated id. |
| `documents/<stem>.entities.yaml` | `reflect add --entities`, drain, heuristic extractor | Same stem as the note. Moved and archived together with it. |
| `archived/` | `reflect serve` archive | Note and sidecar move as a pair; restore moves them back. |
| `review-queue/` | `write_flow` (LOW, or no team KB) | Pointer YAML: `slug, title, source, confidence, category, tags, created, queued_at`. |
| `nano_graphrag_cache/` | `reflect add`, `reflect reindex` | Derived. Rebuild with `reflect reindex`. `reflect init` gitignores it. |
| `shards/` | recall scoping | Recall reads the current project or branch shard first, `--global` pools everything. |
| `reflect.db` | `reflect_db.py` | Path from `storage.db_path` in `reflect.toml` (default `~/.reflect/reflect.db`). |
| `<artifacts_dir>/<category>/<slug>.md` | `/reflect` skill | Project-scoped notes. `storage.artifacts_dir` defaults to `docs/solutions`. Updates append to `<slug>.history.yaml`. |

Overrides:

| Variable | Effect |
|---|---|
| `GLOBAL_LEARNINGS_PATH` | Replaces `~/.learnings` as the KB root for the CLI and recall. |
| `REFLECT_LEARNINGS_DIR` | Directory the mini-learning and permission hooks write to (default `~/.learnings/documents`). |

:::note
`reflect init` creates `documents/` and `nano_graphrag_cache/`, runs `git init`, and writes `.gitignore` with `.venv/`, `__pycache__/`, `*.pyc`, `nano_graphrag_cache/`. Notes are meant to be committed; the index is not.
:::

## Ids and file names

One note can carry up to three names, and they are not the same string.

| Writer | File name in `documents/` | `id` in frontmatter |
|---|---|---|
| `reflect add` (any source) | `<slug>-<hash6>.md`: slug = lowercased title, alphanumerics and single hyphens, max 50 chars; `hash6` = first 6 hex of `sha256(title + "\n" + body)` | whatever the source note carries |
| Drain single-shot writer | as above (it indexes through `reflect add --force`) | `lrn-<slug>-<hash6>`: slug max 60 chars; `hash6` = `sha1(title + rule)[:6]` |
| Mini-learning hook | `lrn-mini-<unix-ts>-<session8>.md` | same string |
| Permission-reply hook | `lrn-perm-<unix-ts>-<session8>.md` | same string |
| Fleet importer | content-addressed doc id | `name:` carries the doc id (no `id:`) |

Resolution rules in the readers:

- Recall: `id`, else `name`, else `?`.
- `reflect serve`: `id`, else the file stem.
- Because the hash covers the body, same title and same body is idempotent (re-add overwrites), same title and different body yields a distinct file. A non-TTY `reflect add` onto an existing file exits 2 unless `--force` is passed.
- The sidecar `document_id` for drain output is the note's `lrn-` id, not the file stem.

## Learning note

A note is `---`-fenced YAML frontmatter followed by a markdown body. The body convention is `## Problem`, `## Solution`, `## Anti-Pattern`, `## Context`; no reader requires it.

```markdown
---
type: learning
id: lrn-pool-timeout-on-cold-start-a1b2c3
created: 2026-03-04T09:12:00Z
updated: 2026-03-04T09:12:00Z
scope: global
confidence: high
confidence_num: 0.85
learning_type: bug-fix
discovery_tokens: 14200
title: "Pool timeout on cold start"
tags: ["postgres", "pooling"]
symptoms:
  - "TimeoutError: QueuePool limit of size 5 overflow 10 reached"
key_insight: "Warm the pool before the first request burst."
problem: "First requests after deploy time out."
root_cause: "The pool opens connections lazily under load."
fix: "Open min_size connections during startup."
rule: "Prefill connection pools in the startup hook."
category: "debugging"
entities: ["sqlalchemy", "QueuePool"]
causal_relations:
  - source: "lazy pool init"
    target: "cold start timeout"
    type: caused_by
links: []
source_episodes: [ep-20260304-091200]
superseded_by: null
forget_after: null
provenance:
  source_tool: "claude"
  source_path: "/path/to/transcript.jsonl"
  content_hash: "9f2c..."
  detected_at: 2026-03-04T09:12:00Z
  source_memory_ids: [ep-20260304-091200]
  proof_count: 1
---

## Problem
...
```

The example is invented. The field set mirrors `plugin/assets/learning_template.md`.

### Frontmatter fields

"Required" depends on who is asking, see the next table.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `type` | string | no | `learning` (also `observation`, `episode`). Recall does not read it; `reflect serve` falls back to it for a type label. |
| `id` | string | no | Stable id, see [Ids](#ids-and-file-names). |
| `created` | ISO timestamp | schema | Capture time. Templates and the drain write a full timestamp. |
| `updated` | ISO timestamp | no | Real content edits only. Ranking never touches it. |
| `scope` | string | no | Free text. The drain writes `global`; `reflect serve` shows `unscoped` when absent. |
| `confidence` | `high` \| `medium` \| `low` | schema | Display bucket. Routing treats `h/m/l/med` aliases and any case, missing means `medium`. |
| `confidence_num` | float 0.0 to 1.0 | no | The value recall ranks by. Buckets: `>=0.8` high, `>=0.5` medium, else low. Legacy tier-only notes use the midpoints 0.9 / 0.6 / 0.3. |
| `learning_type` | string | no | Taxonomy id from the active mode: `pattern`, `correction`, `bug-fix`, `decision`, `anti-pattern`. The drain currently writes `bug-fix` for every note. Selects the glyph and the discovery-cost fallback. |
| `discovery_tokens` | int | no | Tokens the originating session spent finding this out (about transcript chars / 4). Recall shows it in the economics block. Fallback chain: this field, then `provenance.source_path` size / 4, then a per-type average (bug-fix 3000, anti-pattern 2500, correction 2000, pattern 1500, decision 1200, default 1500). |
| `title` | string | schema, `add` | Human title. Readers fall back to `name`, then a heading. |
| `category` | string | `add` | Free-form classifier. Fleet and heuristic extraction also read it. |
| `key_insight` | string | `add` | One-sentence takeaway. |
| `tags` | list of string | no | Retrieval tags. |
| `symptoms` | list of string | no | Error strings and observable signals. |
| `problem` | string | no | One-sentence problem. Recall can return it alone with `--field problem`. |
| `root_cause` | string | no | One-sentence cause. |
| `fix` | string | no | One-sentence resolution. |
| `rule` | string | no | Imperative do or do not. |
| `entities` | list of string | no | Named tech, tools, errors. |
| `causal_relations` | list of `{source, target, type}` | no | Cause to effect edges. The drain accepts the 14 relationship types below and collapses anything else to `relates_to`. |
| `links` | list | no | The template writes `[]`. No reader found in the code. |
| `source_episodes` | list of string | no | Episode ids that produced the note. The template writes it; no reader found in the code. |
| `superseded_by` | string or null | no | Id of the replacing note. Recall drops a note where this is set, see [Supersession](#archive-forget-and-supersession). |
| `forget_after` | ISO timestamp or null | no | TTL. The hourly forget sweep archives the note after this instant. `null` is permanent. Unparseable values are treated as permanent. |
| `provenance` | mapping | no | `source_tool` (`claude`, `codex`, `copilot`, `gemini`), `source_path`, `content_hash`, `detected_at`, `source_memory_ids` (unique), `proof_count` (starts at 1, update increments). Recall reads `provenance.proof_count` when the top-level field is absent. |
| `language`, `framework` | string | no | Primary language or runtime. Written by the `/reflect` skill when known. |
| `unverified_refs` | list of string | no | Commit-like refs in the body that do not exist in the project repo. A note where every ref is fabricated is rejected at write. |
| `source_path`, `session_id` | string | no | Written by the drain and the hooks. |

Fields that only some writers produce:

| Field | Writer | Meaning |
|---|---|---|
| `name`, `source_system`, `source_kind`, `content_hash`, `workflow_state`, `occurrences`, `supersedes` | Fleet importer | Imported fleet notes. |
| `authority` | Fleet importer, hand edit | `law` and `promoted` outrank `advisory`, which outranks `archived`. Absent is neutral. |
| `quarantine` | Fleet importer | `true` keeps the note out of claude and codex recall scope until promoted. |
| `domain` | Fleet importer | Domain bucket matched by `--domain-hint`. |
| `project`, `project_id`, `agent`, `date` | hand or importer | Optional; recall reads them for project affinity and dating. |
| `captured_at`, `source` | Hooks | Hook-captured notes (`posttooluse-minilearning`, `permission-pattern`). |

### Fields that must never appear

Rule S9: frontmatter is semantic and immutable after write. These change on every query and live only in the `learning_signals` table in `reflect.db`:

`importance`, `maturity`, `recall_count`, `helpful_count`, `ignored_count`, `stale_count`, `last_recalled_at`

Writing them into notes would dirty git on every recall and conflict across teammates. Note writers strip them.

### Who requires what

| Consumer | Required keys | Failure mode |
|---|---|---|
| `reflect add` | `title`, `category`, `key_insight`, and a frontmatter block | Prints an error and writes nothing. |
| `schemas/frontmatter.schema.json` (pre-commit and CI) | `title`, `created`, `confidence` | Validation error. Extra keys are allowed. |
| Recall | none | Missing `confidence` is `MEDIUM`, missing title falls back to `name` then `(no title)`. |
| Write routing (`write_flow`) | none | Missing `confidence` routes as `medium`. |

:::caution
`scripts/validate_frontmatter.py` checks `created` as a string with format `date`. Run against a note with an unquoted `created: 2026-03-04` (YAML parses it as a date object) or a full timestamp, it fails with `is not of type 'string'`. The drain and the template write unquoted timestamps, so the schema suits hand-authored team-KB notes with `created: "2026-03-04"` rather than machine-written ones. Verified by running the script against sample files.
:::

### Confidence and team routing

`write_flow.route_document` dispatches on the `confidence` tier:

| Tier | Route |
|---|---|
| `high` | Copy note and sidecar into the team KB `documents/`, commit `feat(knowledge): <title> [HIGH]`, push to `main`. |
| `medium` or missing | Branch `knowledge/<slug>`, commit `docs(knowledge): <title> [MED]`, push, open a draft PR when `gh` is available. |
| `low`, or no team KB configured | Write a pointer to `~/.learnings/review-queue/<slug>.yaml`. |

The source note is never rewritten by routing.

## Entity sidecar

`<stem>.entities.yaml` seeds the graph. `reflect add --entities FILE` copies the file next to the note. Without it, `reflect add` runs a heuristic extractor (no LLM) and writes a sidecar only if it finds entities. `reflect generate-sidecars` backfills missing ones.

```yaml
document_id: lrn-pool-timeout-on-cold-start-a1b2c3
extracted_at: "2026-03-04T09:12:00Z"
entities:
  - name: "sqlalchemy"
    type: technology
    description: "Python SQL toolkit"
  - name: "queuepool limit error"
    type: error
    description: "Pool exhausted before connections warmed"
relationships:
  - source: "lazy pool init"
    target: "queuepool limit error"
    type: caused_by
    description: "Connections open lazily under load"
    strength: 8
    tcommit: "2026-03-04T09:12:00Z"
    tvalid: "2026-01-10"
```

| Key | Required | Notes |
|---|---|---|
| `document_id` | strict mode | Matches the note `id`. |
| `extracted_at` | strict mode | Ingest time. Default for `tcommit`. |
| `entities[].name` | yes | Normalize to lowercase canonical form. |
| `entities[].type` | yes | `technology`, `error`, `pattern`, `function`, `concept`, `tool`. |
| `entities[].description` | yes | May be empty. |
| `relationships[].source`, `.target` | yes | Use `source` and `target`; `from` and `to` are rejected. Strict mode requires both to be listed entity names. |
| `relationships[].type` | yes | Closed enum below. |
| `relationships[].description` | yes | May be empty. |
| `relationships[].strength` | no | Integer 1 to 10, default 5. 9 to 10 direct, 5 to 7 moderate, 1 to 4 weak. |
| `tcommit`, `tvalid`, `tvalid_end` | no | ISO 8601 date or datetime. See below. |
| `superseded_by` | no | Free string naming the edge that replaced this one. |

Relationship types (14):

| Group | Types |
|---|---|
| Typed causal links (preferred) | `caused_by`, `causes`, `enables`, `prevents`, `contradicts`, `supersedes`, `part_of`, `uses` |
| Legacy, still valid | `solves`, `requires`, `relates_to`, `implements`, `configures`, `triggers` |

Prefer a causal type whenever direction is known; `relates_to` is the undirected fallback and the backfill default.

Validate with `plugin/scripts/validate_sidecar.py`:

| Flag | Effect |
|---|---|
| (none) | Checks structure, closed enums, ISO clocks, and `tvalid_end >= tvalid`. Exit 0 valid, 1 invalid. |
| `--strict` | Also requires `document_id` and `extracted_at`, strength int 1 to 10, and known edge endpoints. |
| `--backfill` | Rewrites missing or unknown relationship types to `relates_to` in place. |
| `--backfill-tcommit` | Stamps `tcommit` = `extracted_at` on edges that lack it. |

:::caution
Two enums disagree. The validator accepts the 6 entity types above, but the heuristic extractor in `entity_store.py` also emits `artifact`, `code`, `config`, `service`, `platform`, `framework`, `library`. A sidecar produced by the heuristic path can fail `validate_sidecar.py`. The drain writer emits every entity as `technology` with an empty description.
:::

### Bitemporal edge clocks

| Field | Meaning | Default |
|---|---|---|
| `tcommit` | When reflect learned the edge | sidecar `extracted_at` |
| `tvalid` | When it became true in the world | `tcommit` |
| `tvalid_end` | When it stopped being true | absent means still valid |

Supersede an edge by setting `tvalid_end` and `superseded_by` on the old one rather than deleting it. `recall.py` defines `filter_edges_by_tvalid` for date-range queries (overlap test on `[tvalid, tvalid_end]`, disabled with `RECALL_BITEMPORAL_EDGES=0`), but I found no call site for it in the recall pipeline other than the A2 behavioural proof. Treat the clocks as stored and validated, not yet applied to live queries.

## Archive, forget, and supersession

Four mechanisms retire a note. They are separate and they do not share storage.

```
        ┌────────────────────┐   move pair        ┌──────────────┐
 serve  │ documents/<id>.md  │ ─────────────────▶ │ archived/    │
 archive│ + .entities.yaml   │ ◀───────────────── │              │
        └────────────────────┘      restore       └──────────────┘
        ┌────────────────────┐   forget sweep     ┌──────────────┐
 TTL    │ artifact + sidecar │ ─────────────────▶ │ .forgotten/  │
        └────────────────────┘  (+ DB archived)   └──────────────┘
```

| Mechanism | Trigger | Effect on files | Effect on DB |
|---|---|---|---|
| Soft archive | `POST /api/memories/<id>/archive` in `reflect serve` | Note and sidecar move to `archived/`. Refuses if the destination exists; rolls back the note move if the sidecar move fails. | none |
| Restore | `POST /api/memories/<id>/restore` | Pair moves back to `documents/`. Refuses if a live note holds the name. | none |
| TTL forget | `forget_after` passed, hourly `reflect_forget_sweep.py` (`--dry-run`, `--now`) | Artifact and sidecar move to a `.forgotten/` sibling directory (collisions get `.1`, `.2`, ... suffixes). Best effort. | History snapshot, `status` to `archived`, `is_latest = 0`, `learning_forgotten` event. |
| Contradiction | A new learning whose title negates a similar older one (Jaccard above 0.9 and a negation marker in exactly one title) | none | Older row: history snapshot, `is_latest = 0`, `superseded_by_learning_id` set, `contradiction_detected` event. |
| Belief-revision DELETE | Drain `DELETE` action | none | `status` to `reverted`, row kept so the reason stays queryable. |

Notes for each:

- Archiving drops the note from the file-based recall corpus at once. The graph cache still returns it until `reflect reindex`, which `reflect serve` flags with `graph_index_stale: true`, but `recall.py` drops any candidate whose id matches a note in `archived/` or `documents/.forgotten/`, so a stale cache does not put it back in front of the model. `reflect search` warns when `documents/` is newer than the graph cache.
- `archived/` (serve soft-delete) and `.forgotten/` (sweep) are deliberately separate; the sweep has its own DB accounting.
- Supersession is recorded in the database (`is_latest`, `superseded_by_learning_id`, `supersedes_learning_id`). Nothing rewrites frontmatter `superseded_by` (S9, notes stay immutable), but you can set it by hand or from a tool. `recall.py` drops a candidate that has `superseded_by` set, a `status` of `superseded` or `archived`, an id found in `archived/` or `.forgotten/`, or a matching ledger row with `is_latest = 0` (or status `superseded` or `archived`). The ledger match goes through the note at the row's `artifact_path` or a shared `content_hash`; a row with neither cannot be linked to a note file. `REFLECT_RECALL_INCLUDE_SUPERSEDED=1` disables the filter. See [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/).
- Ledger statuses (`learnings.status`): `detected`, `pending`, `proposed`, `approved`, `materialized`, `indexed`, `recalled`, `superseded`, `reverted`, `rejected`, `archived`.
- Updates to `/reflect` project notes keep the previous form in an append-only `<slug>.history.yaml` (`snapshot_at`, `reason`, `content_hash`, `previous` as a literal block), so a git diff shows one added entry per update. Database-side history goes to `learning_history`.

## Reading the KB without reflect

Everything above is YAML and markdown, so `rg`, `yq`, and `git` work. The only opaque parts are `reflect.db` (SQLite) and `nano_graphrag_cache/` (derived). `plugin/scripts/kb_export.py` (run it directly with `python`) writes a deterministic tarball of `documents/` plus a filtered copy of `reflect.db`. It skips the index; `kb_import.py` rebuilds it by reindexing.
