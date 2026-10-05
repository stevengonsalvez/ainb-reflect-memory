---
type: learning
id: lrn-auth-jwt-rs256-jwks-rotation-7c4e8c
created: '2026-07-08'
updated: '2026-09-02'
scope: project-acme-api
confidence: high
confidence_num: 0.94
learning_type: architecture-decision
discovery_tokens: 7800
title: Verify JWTs with RS256 and a cached JWKS endpoint
tags: [auth, jwt, security, rotation]
symptoms: [invalid signature after key rotation, every service holds the signing secret]
key_insight: Sign with a private key held by the auth service; every other service verifies with public keys fetched from JWKS by kid.
problem: HS256 shared secret was copied into six services and could not be rotated without a deploy.
root_cause: Symmetric signing makes every verifier also a potential issuer.
fix: Switch to RS256, publish /.well-known/jwks.json, cache keys for 10 minutes keyed by kid.
rule: Reject tokens whose alg is not in an explicit allow-list.
category: security
entities: [JWT, RS256, JWKS, HS256, acme-api, widgetly]
causal_relations:
- {source: RS256 with JWKS, target: key rotation without deploy, type: enables}
links: [lrn-auth-jwt-hs256-shared-secret-357866]
source_episodes: [ep-da060812]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-07-08-auth-jwt-rs256-jwks-rotation.jsonl
  content_hash: 2b33f6547d57569e
  detected_at: '2026-07-08T11:20:00'
  source_memory_ids: [ep-da060812]
  proof_count: 5
---

## Solution

```ts
const jwks = createRemoteJWKSet(new URL(process.env.JWKS_URL!), {
  cacheMaxAge: 10 * 60 * 1000,
});
const { payload } = await jwtVerify(token, jwks, {
  algorithms: ["RS256"],          // never trust the header's alg
  issuer: "https://auth.acme.test",
  audience: "acme-api",
});
```

Rotation: publish the new key alongside the old one, start signing with the new `kid`, remove the old after the longest token TTL.
