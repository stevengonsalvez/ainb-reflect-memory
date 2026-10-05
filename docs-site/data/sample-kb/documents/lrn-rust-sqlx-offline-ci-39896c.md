---
type: learning
id: lrn-rust-sqlx-offline-ci-39896c
created: '2026-04-14'
updated: '2026-06-02'
scope: project-acme-api
confidence: high
confidence_num: 0.92
learning_type: bug-fix
discovery_tokens: 37100
title: sqlx query! macros need SQLX_OFFLINE in CI
tags: [rust, sqlx, ci, postgres]
symptoms:
- "error: error communicating with database: Connection refused"
- cargo build fails in CI only
key_insight: Commit .sqlx/ query metadata and build with SQLX_OFFLINE=true so CI never needs a live database.
problem: cargo build passes locally but fails on GitHub Actions at the first query! macro.
root_cause: sqlx query! checks SQL against DATABASE_URL at compile time and CI has no database.
fix: Run cargo sqlx prepare locally, commit .sqlx/, set SQLX_OFFLINE=true in the workflow.
rule: Always re-run cargo sqlx prepare after touching any query! string.
category: build-errors
entities: [sqlx, Rust, PostgreSQL, GitHub Actions, cargo, acme-api]
causal_relations:
- {source: missing .sqlx metadata, target: build failure in CI, type: caused_by}
links: []
source_episodes: [ep-eda19084]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-04-14-rust-sqlx-offline-ci.jsonl
  content_hash: 9fe9f7335b325b7a
  detected_at: '2026-04-14T09:12:00'
  source_memory_ids: [ep-eda19084]
  proof_count: 3
---

## Problem

`cargo build` is green locally and red in CI with `error communicating with database`.
The `query!` family of macros connects to `DATABASE_URL` at compile time.

## Solution

```bash
cargo sqlx prepare --workspace   # writes .sqlx/query-*.json
git add .sqlx
```

```yaml
# .github/workflows/ci.yml
env:
  SQLX_OFFLINE: "true"
```

## Anti-Pattern

Spinning up a Postgres service container only to satisfy the compiler. It works, but doubles CI time and hides stale metadata.
Add `cargo sqlx prepare --check` as a CI step so stale `.sqlx/` fails the build.
