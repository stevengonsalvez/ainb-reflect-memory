# What leaves the machine

One page for the security review. reflect is local first: the markdown
knowledge base under `~/.learnings` is the source of truth and every
embedding, entity extraction and ranking step runs on the machine that holds
it. Below is every path by which bytes can leave that machine, what travels,
what stops it, and how to turn it off.

```
┌──────────────┐   transcript slice    ┌──────────────────┐
│  drain (bg)  │──────────────────────▶│ Anthropic API     │  1. Claude drain
└──────────────┘   via `claude -p`     └──────────────────┘
┌──────────────┐   model name only     ┌──────────────────┐
│ first index  │──────────────────────▶│ Hugging Face hub  │  2. model weights (inbound)
└──────────────┘                       └──────────────────┘
┌──────────────┐   redacted notes,     ┌──────────────────┐
│ Mode 2 write │──────────────────────▶│ Supabase Postgres │  3. optional shared store
└──────────────┘   vectors, graph      └──────────────────┘
┌──────────────┐   evidence pack only  ┌──────────────────┐
│ Context      │◀──────────────────────│ authenticated     │  4. broker (serves 3, adds nothing)
│ Broker       │──────────────────────▶│ caller            │
└──────────────┘                       └──────────────────┘
```

## 1. The Claude drain

`plugin/hooks/reflect-drain-bg.sh` runs after a session starts and turns queued
transcripts into learning notes by calling the `claude` CLI headlessly. The
slice of the transcript that carries signal (not the whole transcript) is sent
to Anthropic's API under your Claude subscription, and the model's answer comes
back as JSON actions that are executed locally.

What travels: the gated transcript slice (or the bounded transcript on the
extract path) plus the related-learning titles used for belief revision.
Redaction runs in both directions: the slice and the bounded input are passed
through the secret redactor before they leave the machine, and `redact_secrets`
runs again on every note before `reflect add` writes it. Redaction is
pattern-based; a credential in a shape the tables do not know can still be in
the prompt. If a transcript must never reach the API, delete its queue entry or
set `REFLECT_DISABLED=1`.

**Permission surface.** The writer used to run with `bypassPermissions`, which
granted the headless model every tool with no prompt. Now:

- The default writer is the single-shot extract path: `--tools ""` plus
  `--strict-mcp-config`, so it has no tools at all, one turn.
- The agentic fallback (`REFLECT_DRAIN_WRITER=agentic`) runs with the
  explicit `--permission-mode default`, which takes precedence over the
  operator's settings.json `defaultMode` (proven live against a
  `bypassPermissions` HOME), plus its own allow rules passed via `--settings`
  and `--strict-mcp-config`. Setting sources stay loaded so `/reflect` and
  personal skills resolve. The rules and their override variable are
  documented once, in `plugin/hooks/README.md` (circuit-breaker table); they
  are not repeated here.
- A denied tool call is logged with the tool and command and counted in the
  ledger. A denial on a step the drain does not grant (git commit, memory
  files, the skill-index loop) never changes the outcome; a denial on a call
  the rules should cover poisons the entry as `allowlist_misconfigured`
  instead of re-billing it.
- Whatever file the writer is pointed at is redacted first: the cascade
  slice and the bounded copy where they are cut, and the raw transcript
  (cascade off, missing or crashed) as a redacted copy; if that copy cannot
  be made the entry stays queued.

The compat gate (`tests/compat/test_drain_permissions_live.py`) asserts this
against the real CLI when a key is present.

Off switch: `REFLECT_DISABLED=1` (everything) or `REFLECT_DRAIN_DRY_RUN=1`
(log, do not call the model).

### Every `claude -p` path

| Path | When | What travels | Tools |
|---|---|---|---|
| drain, extract writer (`plugin/scripts/drain_extract.py`) | default drain | redacted bounded transcript, related titles | none (`--tools ""`) |
| drain, agentic writer (`plugin/hooks/lib/writer_argv.sh`) | `REFLECT_DRAIN_WRITER=agentic` | redacted transcript slice | hook-owned allow rules, default mode |
| recall HyDE (`plugin/skills/recall/scripts/recall.py`) | `REFLECT_RECALL_HYDE=1`, off by default | the recall query text | none (`--tools ""`, one turn) |
| issues analyzer (`src/reflect_kb/issues/analyze.py`) | `reflect issues run`, manual | distilled transcript timelines | none (`--tools ""`, one turn) |

## 2. Model weights (inbound, once)

The first `reflect reindex` downloads the pinned embedding model
(`all-mpnet-base-v2`) and, if reranking is enabled, a cross-encoder from the
Hugging Face hub. Only the model name and standard HTTP headers go out; no note
content is involved. After the download everything runs offline. Pre-seed the
Hugging Face cache on an air-gapped machine to avoid this path entirely.

## 3. Optional shared Postgres (Mode 2)

Unset `REFLECT_PG_DSN` and nothing here happens. When set, the derived store
(vectors, entity graph, community reports, memory items) is written to the
configured Postgres over whatever transport the DSN names. Transport security
is the DSN's `sslmode`. Both the writer path and the broker refuse a DSN to a
remote host without `sslmode=require` (or `verify-ca`, `verify-full`); loopback
and Unix-socket servers pass, and `REFLECT_PG_ALLOW_INSECURE=1` is the single
opt-out for anything else (`src/reflect_kb/postgres/dsn.py`). Supabase
connection strings carry TLS; check yours.
The database is dumb: it stores, scopes by `workspace_id` and searches. No LLM
or embedding call is made from the server.

Guards on this path:

- Row-Level Security is FORCEd (migration 0003) on all seven tables
  (`memory_items`, `entities`, `edges`, `ng_kv`, `ng_graph_nodes`,
  `ng_graph_edges`, `ng_vectors`), so even the table owner cannot read across
  workspaces; only superusers and BYPASSRLS roles (Supabase `service_role`)
  are exempt, and that is the trusted worker.
- Classification floor: items labelled `restricted` or `pii` are refused by
  `InsertMemoryInput`, by the nano-graphrag write path (`insert_document` and
  the `ng_kv` full-document store), and by a check constraint on
  `memory_items`. They stay in the local markdown store.
- Every note is redacted before it is written locally, so the shared copy
  inherits that redaction.

## 4. The Context Broker

`python -m reflect_kb.broker` serves `GET|POST /v1/evidence` over the shared
store on its own `REFLECT_BROKER_PG_DSN` (the `reflect_broker` role; a
superuser, BYPASSRLS or owner role is refused at startup). What it returns is an `EvidencePack` (lexical hits, entity matches, a
graph neighborhood, citations) and never a synthesized answer. The tenant is
the verified claim (a UUID, else 403); the body and query string cannot name
one, and each request binds it with `SET LOCAL app.current_workspace`.

Egress from the broker host:

- Outbound to the OIDC issuer: discovery once at startup, then the JWKS
  document again whenever the cached copy is older than the TTL (300 s by
  default) at the time of a request, or on an unknown `kid` (a negative cache
  and a 30 s refresh floor bound the rate). No token or query content
  travels; only the issuer URL is fetched.
- With `REFLECT_BROKER_RESOLVER=http`, the repo name, commit sha and file path
  of every candidate hit are sent to the forge named by
  `REFLECT_BROKER_FORGE_URL_TEMPLATE` to confirm the pin (path
  percent-encoded, traversal rejected). Note content does not travel. The
  default `git` resolver makes no network call; it reads local checkouts.

The refusal rules (401, 403, dropped and counted hits, edges, limits) live in
one place: README, "Context Broker", the table "What is refused, and why".
Configuration and an Entra ID example are in the same section.

## What never leaves

- The markdown notes themselves, unless you opt into Mode 2 or run the broker.
- Embeddings, entity extraction, clustering, reranking: all local.
- The `reflect serve` memory browser binds loopback with no authentication and
  is meant for the machine that owns the KB; it is not an egress path unless
  you front it with something that is. See `docs/reflect-serve.md`.
