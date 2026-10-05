---
type: learning
id: lrn-k8s-readiness-probe-slow-start-84338b
created: '2026-08-12'
updated: '2026-08-12'
scope: project-acme-api
confidence: high
confidence_num: 0.85
learning_type: best-practice
discovery_tokens: 26600
title: Gate traffic with a readiness probe and a startupProbe for slow boots
tags: [k8s, probes, deploy]
symptoms: [502s during rolling deploy, CrashLoopBackOff caused by liveness kills during warmup]
key_insight: Readiness controls traffic, liveness restarts; a startupProbe keeps liveness quiet until warmup ends.
problem: Pods were killed while loading the sqlx pool and caches, looping forever.
root_cause: Liveness probe started at 5s but the app needs ~25s.
fix: Add startupProbe with failureThreshold 30, keep liveness lenient, readiness hits /readyz.
rule: Liveness must never depend on downstreams like the database.
category: devops
entities: [Kubernetes, readiness probe, acme-api, PostgreSQL]
causal_relations:
- {source: liveness during warmup, target: CrashLoopBackOff, type: causes}
links: []
source_episodes: [ep-0e7d188d]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-08-12-k8s-readiness-probe-slow-sta.jsonl
  content_hash: 56a3f5b418e18df9
  detected_at: '2026-08-12T09:15:00'
  source_memory_ids: [ep-0e7d188d]
  proof_count: 2
---

```yaml
startupProbe:  { httpGet: { path: /healthz, port: 8080 }, periodSeconds: 2, failureThreshold: 30 }
readinessProbe: { httpGet: { path: /readyz,  port: 8080 }, periodSeconds: 5 }
livenessProbe:  { httpGet: { path: /healthz, port: 8080 }, periodSeconds: 10 }
```

`/healthz` returns 200 if the process is up; `/readyz` also checks the DB pool.
