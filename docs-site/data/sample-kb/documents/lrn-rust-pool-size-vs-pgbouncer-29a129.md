---
type: learning
id: lrn-rust-pool-size-vs-pgbouncer-29a129
created: '2026-08-18'
updated: '2026-09-20'
scope: project-acme-api
confidence: high
confidence_num: 0.93
learning_type: architecture-decision
discovery_tokens: 11400
title: Size the sqlx pool from PgBouncer limits, not from load
tags: [rust, sqlx, postgres, pgbouncer, perf]
symptoms:
- PoolTimedOut after 30s
- "FATAL: sorry, too many clients already"
key_insight: Total connections = replicas x max_connections must stay under PgBouncer default_pool_size; extra connections only queue.
problem: PoolTimedOut errors under load even after raising max_connections.
root_cause: Eight replicas x 50 connections exceeded the PgBouncer pool of 100, so most sat waiting.
fix: PgPoolOptions::max_connections(10) per replica, transaction pooling mode, statement cache off.
rule: Pool size is a budget shared across replicas, not a per-pod dial.
category: performance
entities: [connection pool, sqlx, PgBouncer, PostgreSQL, Kubernetes, acme-api]
causal_relations:
- {source: replicas x pool size > server limit, target: PoolTimedOut, type: causes}
- {source: lower per-replica pool, target: stable latency, type: enables}
links: [lrn-pg-raise-max-connections-455dff]
source_episodes: [ep-4efd1e93]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-08-18-rust-pool-size-vs-pgbouncer.jsonl
  content_hash: 9d8e844f56a15b1a
  detected_at: '2026-08-18T14:45:00'
  source_memory_ids: [ep-4efd1e93]
  proof_count: 4
---

## Problem

`PoolTimedOut` at p99 even after bumping `max_connections` to 50.

## Solution

```rust
let pool = PgPoolOptions::new()
    .max_connections(10)                 // 8 replicas x 10 = 80 < 100
    .acquire_timeout(Duration::from_secs(3))
    .connect(&url).await?;
```

PgBouncer in `pool_mode = transaction`; with it, disable prepared-statement caching:
`PgConnectOptions::statement_cache_capacity(0)`.

## Context

Replaces the earlier "raise Postgres max_connections" advice, which only moved the queue into the database.
