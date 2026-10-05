---
type: learning
id: lrn-auth-refresh-token-rotation-reuse-831860
created: '2026-07-29'
updated: '2026-07-29'
scope: project-widgetly
confidence: high
confidence_num: 0.86
learning_type: architecture-decision
discovery_tokens: 46800
title: Rotate refresh tokens and revoke the family on reuse
tags: [auth, security, oauth, refresh-token]
symptoms: [stolen refresh token valid for 30 days]
key_insight: "Single-use refresh tokens let you detect theft: a second use of a spent token means someone copied it."
problem: Long-lived static refresh tokens gave attackers a silent 30-day foothold.
root_cause: No way to distinguish the legitimate client from a replaying attacker.
fix: Issue a new refresh token per refresh, store a family id, revoke the whole family on reuse.
rule: "Refresh endpoints must be atomic: compare-and-swap the token hash."
category: security
entities: [refresh token rotation, OAuth, JWT, widgetly, PostgreSQL]
causal_relations:
- {source: token reuse, target: family revocation, type: causes}
links: []
source_episodes: [ep-bd5acd78]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-07-29-auth-refresh-token-rotation-.jsonl
  content_hash: 097c75d9214914bf
  detected_at: '2026-07-29T13:40:00'
  source_memory_ids: [ep-bd5acd78]
  proof_count: 2
---

```sql
UPDATE refresh_tokens
SET used_at = now()
WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now()
RETURNING family_id;   -- zero rows => reuse or expiry => revoke family
```

Allow a ~10 s grace window for multi-tab races, otherwise honest users get logged out.
