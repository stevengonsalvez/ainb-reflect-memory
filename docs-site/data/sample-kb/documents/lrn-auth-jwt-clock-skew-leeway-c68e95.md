---
type: learning
id: lrn-auth-jwt-clock-skew-leeway-c68e95
created: '2026-06-28'
updated: '2026-06-28'
scope: cross-project
confidence: high
confidence_num: 0.9
learning_type: bug-fix
discovery_tokens: 48400
title: Allow 30s of clock-skew leeway when verifying exp and nbf
tags: [auth, jwt, debugging]
symptoms:
- "JWTClaimValidationFailed: nbf claim timestamp check failed"
- intermittent 401 right after login
key_insight: Freshly issued tokens can look not-yet-valid to a node whose clock lags by a second or two.
problem: Roughly 1 in 200 logins returned 401 on the very next request.
root_cause: Verifier node clock was ~1.5s behind the issuer and nbf was set to the issue time.
fix: Configure clockTolerance of 30 seconds and run chrony on all nodes.
rule: Never verify time-bound claims without leeway.
category: debugging-sessions
entities: [JWT, acme-api, Kubernetes]
causal_relations:
- {source: clock drift, target: nbf rejection, type: causes}
links: []
source_episodes: [ep-64a45a3c]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/cross-project/2026-06-28-auth-jwt-clock-skew-leeway.jsonl
  content_hash: 1909792a20d24ba1
  detected_at: '2026-06-28T16:05:00'
  source_memory_ids: [ep-64a45a3c]
  proof_count: 3
---

```ts
await jwtVerify(token, jwks, { clockTolerance: "30s" });
```

Check drift with `chronyc tracking`. Anything over 100 ms between nodes is worth alerting on.
