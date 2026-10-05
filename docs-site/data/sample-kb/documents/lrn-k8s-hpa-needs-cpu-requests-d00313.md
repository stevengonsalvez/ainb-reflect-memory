---
type: learning
id: lrn-k8s-hpa-needs-cpu-requests-d00313
created: '2026-09-08'
updated: '2026-09-08'
scope: project-acme-api
confidence: medium
confidence_num: 0.63
learning_type: gotcha
discovery_tokens: 8700
title: HPA shows <unknown> targets until pods declare CPU requests
tags: [k8s, hpa, autoscaling]
symptoms:
- kubectl get hpa shows TARGETS <unknown>/70%
- "unable to compute replica count: missing request for cpu"
key_insight: Utilization is a percentage of the request, so no request means no metric and no scaling.
problem: Autoscaler never scaled the API despite 95 percent CPU.
root_cause: Container spec had limits but no requests.
fix: Set resources.requests.cpu and memory; verify with kubectl describe hpa.
rule: Always set requests; set memory limit equal to request.
category: debugging-sessions
entities: [HPA, Kubernetes, OOMKilled, acme-api]
causal_relations:
- {source: no cpu request, target: HPA unknown, type: causes}
links: []
source_episodes: [ep-eed89961]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/acme-api/2026-09-08-k8s-hpa-needs-cpu-requests.jsonl
  content_hash: fbb3c4a422d96c90
  detected_at: '2026-09-08T14:20:00'
  source_memory_ids: [ep-eed89961]
  proof_count: 1
---

```yaml
resources:
  requests: { cpu: 250m, memory: 256Mi }
  limits:   { memory: 256Mi }
```

Memory limit above the request invites OOMKilled once the node is under pressure.
