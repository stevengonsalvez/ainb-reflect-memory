---
type: learning
id: lrn-pg-idle-in-transaction-timeout-d97f45
created: '2026-06-11'
updated: '2026-06-11'
scope: project-acme-api
confidence: medium
confidence_num: 0.58
learning_type: gotcha
discovery_tokens: 13600
title: Set idle_in_transaction_session_timeout to stop lock leaks
tags: [postgres, locks, config]
symptoms: [ALTER TABLE hangs forever, pg_stat_activity shows state = 'idle in transaction']
key_insight: A request that opens a transaction and then crashes or awaits an HTTP call keeps its locks until the session dies.
problem: A migration blocked behind an abandoned transaction for 40 minutes.
root_cause: Handler held a transaction open across an outbound HTTP call that never returned.
fix: Set idle_in_transaction_session_timeout = '30s' and move HTTP calls outside the transaction.
rule: Never await network I/O while holding a transaction.
category: debugging-sessions
entities: [PostgreSQL, idle_in_transaction_session_timeout, migrations, acme-api, PgBouncer]
causal_relations:
- {source: idle transaction, target: blocked migration, type: causes}
- {source: session timeout, target: lock release, type: prevents}
links: []
source_episodes: [ep-2885aa98]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/acme-api/2026-06-11-pg-idle-in-transaction-timeo.jsonl
  content_hash: 7eea2bc4e9c03664
  detected_at: '2026-06-11T12:15:00'
  source_memory_ids: [ep-2885aa98]
  proof_count: 1
---

## Diagnosis

```sql
SELECT pid, now() - xact_start AS age, query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY age DESC;
```

## Fix

```sql
ALTER ROLE acme_app SET idle_in_transaction_session_timeout = '30s';
```
