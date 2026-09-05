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

What travels: the gated transcript slice plus the related-learning titles used
for belief revision. Redaction happens on the way back, not on the way out:
`redact_secrets` runs on every note before `reflect add` writes it, so a
credential that was in the transcript cannot land in the knowledge base. It
does not stop the credential from having been in the prompt. If a transcript
must never reach the API, delete its queue entry or set `REFLECT_DISABLED=1`.

**The bypassPermissions caveat.** Until this change the agentic writer ran
`claude -p ... --permission-mode bypassPermissions`, which granted the headless
model every tool with no prompt: arbitrary Bash, network, writes anywhere. The
default writer is now the single-shot extract path, which runs with
`--allowedTools ""` (no tools at all, one turn). The agentic fallback runs with
`--allowedTools "$REFLECT_DRAIN_ALLOWED_TOOLS"` (default `Read, Grep, Glob,
Write, Edit, Bash(reflect:*), Bash(python3:*)`) and no permission mode flag, so
headless mode denies anything outside that list. Neither path uses
`bypassPermissions` any more.

Off switch: `REFLECT_DISABLED=1` (everything) or `REFLECT_DRAIN_DRY_RUN=1`
(log, do not call the model).

## 2. Model weights (inbound, once)

The first `reflect reindex` downloads the pinned embedding model
(`all-mpnet-base-v2`) and, if reranking is enabled, a cross-encoder from the
Hugging Face hub. Only the model name and standard HTTP headers go out; no note
content is involved. After the download everything runs offline. Pre-seed the
Hugging Face cache on an air-gapped machine to avoid this path entirely.

## 3. Optional shared Postgres (Mode 2)

Unset `REFLECT_PG_DSN` and nothing here happens. When set, the derived store
(vectors, entity graph, community reports, memory items) is written to the
configured Postgres over the DSN's TLS connection. The database is dumb: it
stores, scopes by `workspace_id` and searches. No LLM or embedding call is made
from the server.

Guards on this path:

- Row-Level Security is FORCEd (migration 0003), so even the table owner cannot
  read across workspaces; only superusers and BYPASSRLS roles (Supabase
  `service_role`) are exempt, and that is the trusted worker.
- Classification floor: items labelled `restricted` or `pii` are refused by
  `InsertMemoryInput` and by a check constraint on `memory_items`. They stay in
  the local markdown store.
- Every note is redacted before it is written locally, so the shared copy
  inherits that redaction.

## 4. The Context Broker

`python -m reflect_kb.broker` serves `GET|POST /v1/evidence` over the shared
store. It adds no new egress: it reads Postgres and answers an authenticated
caller. What it returns is an `EvidencePack` (lexical hits, entity matches, a
graph neighborhood, citations) and never a synthesized answer.

- No token: 401. Token without the tenant claim: 403. The tenant is the
  verified claim; the body and query string cannot name one.
- Every returned hit carries `repo@sha:path[#Lstart-Lend]` and the resolver has
  confirmed that commit and path exist. Unpinned or unresolvable hits are
  dropped and counted in `meta.dropped`.
- `restricted` and `pii` never appear (defence in depth over the floor above).

Configuration and an Entra ID example are in the README.

## What never leaves

- The markdown notes themselves, unless you opt into Mode 2 or run the broker.
- Embeddings, entity extraction, clustering, reranking: all local.
- The `reflect serve` memory browser binds loopback with no authentication and
  is meant for the machine that owns the KB; it is not an egress path unless
  you front it with something that is. See `docs/reflect-serve.md`.
