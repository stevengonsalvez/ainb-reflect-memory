---
type: learning
id: lrn-pg-index-concurrently-no-transaction-c2645c
created: '2026-06-19'
updated: '2026-06-19'
scope: project-acme-api
confidence: high
confidence_num: 0.9
learning_type: gotcha
discovery_tokens: 41400
title: CREATE INDEX CONCURRENTLY cannot run inside a migration transaction
tags: [postgres, migrations, sqlx, index]
symptoms:
- "ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block"
key_insight: Tell the migration runner to skip its wrapping transaction for that one file.
problem: Index migration fails on apply while the same SQL works in psql.
root_cause: sqlx wraps every migration file in BEGIN/COMMIT by default.
fix: Add the `-- no-transaction` directive on the first line of the migration.
rule: One CONCURRENTLY statement per migration file, nothing else.
category: build-errors
entities: [CREATE INDEX CONCURRENTLY, PostgreSQL, sqlx, migrations, acme-api]
causal_relations:
- {source: implicit transaction, target: CONCURRENTLY failure, type: caused_by}
links: []
source_episodes: [ep-bd70f7cb]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-06-19-pg-index-concurrently-no-tra.jsonl
  content_hash: 0077dfd3ab9f60ac
  detected_at: '2026-06-19T15:30:00'
  source_memory_ids: [ep-bd70f7cb]
  proof_count: 2
---

## Solution

```sql
-- no-transaction
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_customer_id
    ON orders (customer_id);
```

If the build is interrupted it leaves an INVALID index; drop it before retrying:
`DROP INDEX CONCURRENTLY idx_orders_customer_id;`
