---
type: learning
id: lrn-perf-redis-cache-stampede-singleflight-4c1630
created: '2026-09-30'
updated: '2026-10-02'
scope: project-acme-api
confidence: low
confidence_num: 0.49
learning_type: performance
discovery_tokens: 33600
title: Collapse concurrent cache misses with singleflight to stop stampedes
tags: [redis, cache, perf, rust]
symptoms: [database CPU spike every time a hot key expires, p99 jumps at exactly TTL boundaries]
key_insight: When a hot key expires, only one caller should rebuild it; the rest wait on that result.
problem: Expiry of the pricing cache sent 400 identical queries to PostgreSQL in the same second.
root_cause: Every miss recomputed independently, no coalescing and synchronized TTLs.
fix: In-process singleflight keyed by cache key, plus +/-10 percent TTL jitter.
rule: Jitter every TTL; coalesce every expensive miss.
category: performance
entities: [Redis, singleflight, PostgreSQL, Rust, tokio, acme-api]
causal_relations:
- {source: synchronized TTL expiry, target: query stampede, type: causes}
- {source: singleflight, target: load shed, type: prevents}
links: []
source_episodes: [ep-937fd69b]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-09-30-perf-redis-cache-stampede-si.jsonl
  content_hash: 84a940af565646a0
  detected_at: '2026-09-30T10:40:00'
  source_memory_ids: [ep-937fd69b]
  proof_count: 2
---

```rust
let price = flights
    .work(&key, || async { load_price_from_pg(&pool, sku).await })
    .await?;
redis.set_ex(&key, &price, 300 + rand::random::<u64>() % 60).await?;
```

Across replicas you still get one rebuild per replica; for global coalescing take a short `SET NX PX` lock.
