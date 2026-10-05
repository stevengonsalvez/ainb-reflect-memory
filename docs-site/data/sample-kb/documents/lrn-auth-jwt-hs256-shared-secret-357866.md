---
type: learning
id: lrn-auth-jwt-hs256-shared-secret-357866
created: '2026-04-09'
updated: '2026-04-09'
scope: project-acme-api
confidence: low
confidence_num: 0.45
learning_type: architecture-decision
discovery_tokens: 12800
title: Share one HS256 secret across services for JWT verification
tags: [auth, jwt, security]
symptoms: [need to verify bearer tokens in every service]
key_insight: A single JWT_SECRET env var is the quickest way to verify tokens everywhere.
problem: Services need to authenticate the same bearer tokens.
root_cause: No shared verification mechanism.
fix: Set the same JWT_SECRET in every service and verify with HS256.
rule: Keep the secret in the secrets manager.
category: security
entities: [JWT, HS256, acme-api]
causal_relations: []
links: []
source_episodes: [ep-de713a30]
superseded_by: lrn-auth-jwt-rs256-jwks-rotation-7c4e8c
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-04-09-auth-jwt-hs256-shared-secret.jsonl
  content_hash: a8a45145afb2bad9
  detected_at: '2026-04-09T09:00:00'
  source_memory_ids: [ep-de713a30]
  proof_count: 1
---

```ts
jwt.verify(token, process.env.JWT_SECRET!, { algorithms: ["HS256"] });
```

Quick to set up. Downside found later: any service that can verify can also mint tokens, and rotating the secret needs a coordinated deploy.
