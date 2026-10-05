---
type: learning
id: lrn-pg-explain-analyze-buffers-da1acb
created: '2026-07-02'
updated: '2026-07-02'
scope: cross-project
confidence: medium
confidence_num: 0.7
learning_type: best-practice
discovery_tokens: 9900
title: Use EXPLAIN (ANALYZE, BUFFERS) before adding an index
tags: [postgres, perf, explain]
symptoms: [slow list endpoint, Seq Scan on large table]
key_insight: Buffer counts and actual row estimates, not cost numbers, tell you whether an index will help.
problem: Indexes were added on guesswork and a few made writes slower without helping reads.
root_cause: Planner estimates were stale; nobody compared estimated to actual rows.
fix: Run EXPLAIN (ANALYZE, BUFFERS) and ANALYZE the table first.
rule: Capture the plan before and after; paste both in the PR.
category: performance
entities: [EXPLAIN ANALYZE, PostgreSQL, acme-api]
causal_relations: []
links: []
source_episodes: [ep-b2c08236]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-07-02-pg-explain-analyze-buffers.jsonl
  content_hash: 70bc2eae9792b6bc
  detected_at: '2026-07-02T10:00:00'
  source_memory_ids: [ep-b2c08236]
  proof_count: 2
---

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE customer_id = 42 ORDER BY created_at DESC LIMIT 20;
```

Look for `rows=` vs `actual rows=` differing by 10x, and `Buffers: shared read` being large.
If estimates are off, `ANALYZE orders;` and re-run before touching the schema.
