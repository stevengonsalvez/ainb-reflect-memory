---
type: learning
id: lrn-pg-raise-max-connections-455dff
created: '2026-05-27'
updated: '2026-05-27'
scope: project-acme-api
confidence: low
confidence_num: 0.42
learning_type: best-practice
discovery_tokens: 58800
title: Raise Postgres max_connections to fix pool timeouts
tags: [postgres, pool, perf]
symptoms: [PoolTimedOut, too many clients already]
key_insight: Bumping max_connections to 500 relieved pool timeouts at first.
problem: API returns 503s when the pool is exhausted.
root_cause: Database cap too low for peak concurrency.
fix: Set max_connections = 500 in postgresql.conf and restart.
rule: Raise max_connections when clients are refused.
category: performance
entities: [PostgreSQL, connection pool, acme-api]
causal_relations: []
links: []
source_episodes: [ep-4def2a34]
superseded_by: lrn-rust-pool-size-vs-pgbouncer-29a129
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-05-27-pg-raise-max-connections.jsonl
  content_hash: a232ca3349a0a6e6
  detected_at: '2026-05-27T17:00:00'
  source_memory_ids: [ep-4def2a34]
  proof_count: 1
---

## Solution

```sql
ALTER SYSTEM SET max_connections = 500;
-- restart required
```

Worked for two weeks. Memory use per backend (~10 MB) then caused OOM kills on the 4 GB instance.
