---
title: serve HTTP API
description: The JSON endpoints behind reflect serve, with methods, parameters, real response examples, curation semantics and the loopback-only security posture.
sidebar:
  order: 6
---

`reflect serve` runs a stdlib-only HTTP server (`http.server.ThreadingHTTPServer`, no FastAPI or uvicorn) that serves a single-file SPA at `/` and a small JSON API under `/api/`. The SPA is the [memory browser](/ainb-reflect-memory/guides/memory-browser/); this page documents the API it talks to. Source: `src/reflect_kb/serve.py`. Launch flags are in the [CLI reference](/ainb-reflect-memory/reference/cli/#reflect-serve).

```bash
reflect serve                          # http://127.0.0.1:8377
reflect serve --port 8942 --repo /path/to/kb
```

All examples below were captured by running the real handler against a throwaway copy of the repo's e2e fixture KB, never a real `~/.learnings`. Paths and ids are fixture data.

## Security posture

```text
browser/curl ──▶ Host check ──▶ (POST only) X-Reflect check ──▶ handler ──▶ local markdown KB
                 403 if not       403 if header missing
                 loopback
```

| Property | Fact |
|---|---|
| Bind address | `--host` defaults to `127.0.0.1`. The flag is not validated: `--host 0.0.0.0` binds publicly. Do not. |
| Authentication | None. No tokens, cookies or users. |
| Host guard | Every request (GET and POST) must carry a `Host` whose name part is `127.0.0.1`, `localhost`, `[::1]` or `::1`. Anything else gets `403 {"error": "forbidden: non-loopback Host"}`. This defeats DNS rebinding. |
| Mutation guard | Every POST must carry a non-empty `X-Reflect` header, else `403 {"error": "forbidden: missing X-Reflect header"}`. A cross-origin page can only send a custom header via a CORS preflight, and the server never approves one (it sends no CORS headers and answers `OPTIONS` with 501). |
| Read vs write | All GETs are read-only. The four POSTs write to the local KB (see [curation](#curation-endpoints)). There is no delete: archive is a reversible move. |
| Backend | Local markdown KB only. There is no Postgres code path in `serve`. |
| Body edits | Metadata only. Note bodies are never rewritten; only the frontmatter `confidence` line changes. |
| Caching | Responses carry `Cache-Control: no-store`. |
| Logging | One access line per request on stdout (`<client> - <request line>`). |

:::caution
Because of the Host guard, putting a reverse proxy in front only works if the proxy forwards a loopback `Host` header. A proxy that passes the public hostname through will see `403`. The older docs suggested `tailscale serve`; that was not verified against the guard and is not recommended here.
:::

## Conventions

| Topic | Behaviour |
|---|---|
| Content type | JSON responses are `application/json`; `/` is `text/html; charset=utf-8`. |
| Methods | `GET` and `POST` only. `HEAD`, `PUT`, `DELETE`, `OPTIONS` return the stdlib default `501`. |
| Errors | `{"error": "<message>"}` with `403` (guard), `404` (unknown route, memory or action), `400` (curation rejected), `500` (unexpected fault). |
| Pagination | None. `/api/memories` returns every note. |
| Memory id | Frontmatter `id`, else the filename stem. URL-encode ids with special characters. |
| Freshness | The read model reloads whenever `documents/` mtime changes, so edits made on disk show up on the next request. |
| Request body | POST bodies are JSON. A missing or invalid body is treated as `{}`. |

## Endpoint summary

| Method | Path | Purpose | Writes |
|---|---|---|---|
| GET | `/` | SPA (`index.html`) | no |
| GET | `/api/memories` | All memories, frontmatter plus derived fields | no |
| GET | `/api/memories/<id>` | One memory with body, entities, related | no |
| GET | `/api/search?q=...` | Lexical ranking over title, tags, body | no |
| GET | `/api/graph` | Two-layer memory and entity graph | no |
| GET | `/api/stats` | KB counts and metrics op aggregates | no |
| GET | `/api/archived` | Soft-archived notes | no |
| GET | `/api/compress-queue` | Queued compression groups | no |
| POST | `/api/memories/<id>/archive` | Soft-archive a note | yes |
| POST | `/api/memories/<id>/restore` | Restore an archived note | yes |
| POST | `/api/memories/<id>/confidence` | Edit confidence | yes |
| POST | `/api/compress-queue` | Queue a group for compression | yes |

Any other path returns `404 {"error": "not found"}`. Only `/` and `/index.html` serve the SPA; the server has no other static routes.

## Read endpoints

### `GET /api/memories`

Array of memory summaries. No body text.

```json
[
  {
    "id": "alpha-auth-jwt-fix",
    "file": "alpha-auth-jwt-fix.md",
    "title": "Supabase JWT verification fails on clock skew",
    "confidence": "high",
    "type": "bug-fix",
    "scope": "cross-project",
    "tags": ["project-alpha", "auth", "supabase"],
    "date": "2026-06-28",
    "superseded_by": null,
    "provenance": null,
    "key_insight": null,
    "agent": null,
    "entity_names": ["supabase", "jwt"],
    "entity_count": 2,
    "word_count": 21,
    "browse_score": 0.577
  }
]
```

Field derivation:

| Field | Source |
|---|---|
| `id` | frontmatter `id`, else filename stem |
| `title` | frontmatter `title` or `name`, else first `# ` heading, else filename stem |
| `confidence` | frontmatter `confidence`, normalised to `high`, `medium`, `low` or `unknown`. Numeric values map at 0.8 and above to high, 0.5 and above to medium, else low |
| `type` | first of frontmatter `learning_type`, `category`, `type`, else `uncategorized` |
| `scope` | frontmatter `scope`, else `unscoped` |
| `tags` | frontmatter `tags` (list, or comma-separated string) |
| `date` | first of `created`, `captured_at`, `updated` (first 19 chars), else file mtime (UTC ISO) |
| `superseded_by`, `provenance`, `key_insight`, `agent` | frontmatter, passed through (`null` if absent) |
| `entity_names`, `entity_count` | from the `.entities.yaml` sidecar (names lower-cased) |
| `word_count` | words in the body |
| `browse_score` | see below |

`browse_score` is `confidence_weight x exp(-age_days / 180)`, with weights high 1.0, medium 0.7, low 0.4, unknown 0.55. In search it is also multiplied by `1 + 0.5 x (query terms matching a tag)`. It orders the browse list only. It is **not** the recall reranker's score: the real reranker deliberately dropped exponential-decay recency.

### `GET /api/memories/<id>`

One summary plus `body`, the sidecar's `entities` and `relationships`, and up to 6 `related` notes. Unknown id returns `404 {"error": "not found"}`.

```json
{
  "id": "alpha-auth-jwt-fix",
  "title": "Supabase JWT verification fails on clock skew",
  "confidence": "high",
  "type": "bug-fix",
  "tags": ["project-alpha", "auth", "supabase"],
  "date": "2026-06-28",
  "word_count": 21,
  "browse_score": 0.577,
  "body": "# Supabase JWT verification fails on clock skew\n\nTokens rejected when the edge node clock drifts. Allow a 30s leeway window.",
  "entities": [
    {"name": "Supabase", "type": "technology", "description": "Supabase referenced in fixture note"},
    {"name": "JWT", "type": "concept", "description": "JWT referenced in fixture note"}
  ],
  "relationships": [
    {"source": "Supabase", "target": "JWT", "description": "Supabase relates_to JWT"}
  ],
  "related": [
    {"id": "alpha-flaky-playwright", "title": "Flaky Playwright test: await network idle", "confidence": "high", "shared": 3}
  ]
}
```

(Summary fields omitted above for brevity are the same as in the list response.) `related[].shared` is `3 x shared tags + shared entity names`, plus 10 if either note supersedes the other. The list is sorted by `shared` descending, then title.

### `GET /api/search?q=<text>`

BM25-style lexical ranking (k1 = 1.4, b = 0.6) over title, tags and body, with title and tag tokens counted twice. Tokens match `[a-z0-9_./-]{2,}`, lower-cased. Returns summaries with two extra fields, best first, capped at **25** results. There is no `limit` parameter.

| Param | Required | Notes |
|---|---|---|
| `q` | no | Missing or empty (or no valid tokens) returns `[]`. |

```json
[
  {
    "id": "alpha-auth-jwt-fix",
    "title": "Supabase JWT verification fails on clock skew",
    "confidence": "high",
    "tags": ["project-alpha", "auth", "supabase"],
    "match_score": 6.223,
    "browse_score": 0.865
  }
]
```

`match_score` is the lexical relevance. Semantic search stays on `reflect search` (see [CLI](/ainb-reflect-memory/reference/cli/#reflect-search-query)).

### `GET /api/graph`

Two node kinds, two edge kinds.

| Node kind | `id` | Extra fields |
|---|---|---|
| `memory` | `m:<memory id>` | `label` (title), `confidence`, `type`, `doc` (memory id), `score` (unrounded browse score), `degree` |
| `entity` | `e:<lower-cased name>` | `label`, `type` (sidecar type, default `concept`), `degree` |

| Edge kind | Meaning | `w` |
|---|---|---|
| `mention` | memory to entity (from its sidecar) | always `1.0` |
| `relation` | entity to entity, from the indexed graphml | graphml `weight`, default `1.0` |

Relation edges come from `nano_graphrag_cache/graph_chunk_entity_relation.graphml` (falling back to `.graph/graph_chunk_entity_relation.graphml`) and are included only when both endpoints exist as entity nodes from sidecars. Without a graphml you get memory and entity nodes with `mention` edges only.

```json
{
  "nodes": [
    {"id": "m:alpha-auth-jwt-fix", "label": "Supabase JWT verification fails on clock skew", "kind": "memory",
     "confidence": "high", "type": "bug-fix", "doc": "alpha-auth-jwt-fix", "score": 0.5769, "degree": 2},
    {"id": "e:supabase", "label": "Supabase", "kind": "entity", "type": "technology", "degree": 2}
  ],
  "edges": [
    {"s": "m:alpha-auth-jwt-fix", "t": "e:supabase", "w": 1.0, "kind": "mention"},
    {"s": "e:supabase", "t": "e:jwt", "w": 7.0, "kind": "relation"}
  ]
}
```

### `GET /api/stats`

```json
{
  "documents": 7,
  "repo": "/home/you/.learnings",
  "confidence": {"high": 3, "medium": 2, "low": 1, "unknown": 1},
  "types": {"bug-fix": 2, "architecture-decision": 2, "tooling-setup": 1, "reference": 1, "uncategorized": 1},
  "scopes": {"cross-project": 2, "project-alpha": 2, "project-beta": 2, "unscoped": 1},
  "top_tags": {"project-alpha": 3, "project-beta": 2, "cache": 2, "auth": 1},
  "metrics_ops": {"search": 3, "recall_search": 1, "rerank": 1},
  "metrics_errors": 1,
  "with_sidecars": 6
}
```

`top_tags` is the top 20. `metrics_ops` and `metrics_errors` aggregate `<repo>/metrics.jsonl` (op counts, and lines that have an `error` field). Note this is the KB-root file; the CLI's own metrics writer always appends to `~/.learnings/metrics.jsonl`, so the two coincide only for the default root.

### `GET /api/archived`

Soft-archived notes, restore candidates, read from `<repo>/archived/`. Sorted by filename.

```json
[
  {"id": "alpha-flaky-playwright", "title": "Flaky Playwright test: await network idle",
   "confidence": "high", "type": "bug-fix", "scope": "project-alpha"}
]
```

### `GET /api/compress-queue`

```json
{
  "version": 1,
  "groups": [
    {"ids": ["alpha-db-migration-order", "alpha-flaky-playwright"],
     "queued_at": "2026-10-05T16:14:54.790643+00:00", "status": "pending"}
  ]
}
```

An empty or missing queue returns `{"version": 1, "groups": []}`. A malformed `compress-queue.yaml` returns `500` with the parse error (the server refuses to overwrite it).

## Curation endpoints

All require `X-Reflect: <any non-empty value>` and a loopback `Host`. Success bodies carry `"ok": true`. A rejected mutation returns `400 {"error": "..."}`.

Curation is **file-first**: the markdown note is the source of truth, and the in-memory view reloads immediately. The nano-graphrag cache behind `reflect search` is **not** rebuilt, because the engine only supports a full-batch reindex. Archive, restore and confidence responses therefore include `"graph_index_stale": true`. Run `reflect reindex` afterwards.

| Endpoint | File effect |
|---|---|
| archive | Moves `documents/<file>.md` and its `.entities.yaml` into `<repo>/archived/`. Rolls the first move back if the sidecar move fails. Removes the id from pending compress groups and drops groups left with fewer than 2 ids. |
| restore | Moves the pair back into `documents/`. |
| confidence | Rewrites the column-0 `confidence:` frontmatter line (appends one if absent) using an atomic temp-file replace. Splits on real `---` delimiter lines, so a value containing `---` cannot corrupt the note. |
| compress-queue | Appends a group to `<repo>/compress-queue.yaml` (`version: 1`, each group `ids`, `queued_at`, `status: pending`). |

`archived/` is the browser's own reversible delete. It is separate from the forget sweep's `.forgotten/` directory and does not touch the sweep's records.

### `POST /api/memories/<id>/archive`

No body.

```bash
curl -X POST -H 'X-Reflect: 1' http://127.0.0.1:8377/api/memories/alpha-flaky-playwright/archive
```

```json
{"ok": true, "id": "alpha-flaky-playwright", "archived": true, "graph_index_stale": true}
```

Errors (400): `unknown memory: <id>`, `archive already contains <file>`, `destination sidecar already exists: <file>`.

### `POST /api/memories/<id>/restore`

No body.

```json
{"ok": true, "id": "alpha-flaky-playwright", "archived": false, "graph_index_stale": true}
```

Errors (400): `not in archive: <id>`, `a live note already occupies <file>`.

### `POST /api/memories/<id>/confidence`

Body `{"value": "high" | "medium" | "low"}` (trimmed, case-insensitive).

```bash
curl -X POST -H 'X-Reflect: 1' -H 'Content-Type: application/json' \
  -d '{"value":"low"}' http://127.0.0.1:8377/api/memories/alpha-auth-jwt-fix/confidence
```

```json
{"ok": true, "id": "alpha-auth-jwt-fix", "confidence": "low", "graph_index_stale": true}
```

Errors (400): `confidence must be one of ('high', 'medium', 'low')`, `unknown memory: <id>`, `note has no frontmatter to edit`.

### `POST /api/compress-queue`

Body `{"ids": ["<id>", "<id>", ...]}`. Duplicates are collapsed and ids that are not live memories are silently dropped; at least two must remain.

```json
{"ok": true, "queued": ["alpha-db-migration-order", "alpha-flaky-playwright"], "groups": 1}
```

Error (400): `compress needs at least two live memories`. The web app never calls an LLM. It only writes the queue file. Nothing in this repository's plugin reads `compress-queue.yaml` today, so treat it as a handoff file for whatever consolidation step you run.

### Unknown POST targets

`POST /api/memories/<id>/<other>` returns `404 {"error": "unknown action"}`; any other unrouted POST path returns `404 {"error": "not found"}`.

## Error examples

```json
{"error": "forbidden: non-loopback Host"}
```
```json
{"error": "forbidden: missing X-Reflect header"}
```
```json
{"error": "not found"}
```

## Tests

End-to-end coverage lives in [`tests/e2e/`](https://github.com/stevengonsalvez/ainb-reflect-memory/tree/main/tests/e2e) (Playwright against a fresh copy of `tests/e2e/fixture-kb`) and `tests/test_serve_curation.py`.
