---
type: learning
id: lrn-auth-samesite-lax-oauth-redirect-24ec2c
created: '2026-08-05'
updated: '2026-08-05'
scope: project-widgetly
confidence: medium
confidence_num: 0.52
learning_type: gotcha
discovery_tokens: 11100
title: SameSite=Strict cookies break the OAuth redirect back to the app
tags: [auth, cookies, oauth, browser]
symptoms: [session cookie missing right after returning from the identity provider]
key_insight: Cross-site top-level navigations do not send Strict cookies; Lax does for GET.
problem: User lands back on widgetly logged out after a successful Google login.
root_cause: Session cookie was SameSite=Strict so the callback request carried no cookie.
fix: Use SameSite=Lax for the session cookie, keep CSRF tokens for state-changing routes.
rule: Strict is for cookies that never need to survive an inbound cross-site link.
category: debugging-sessions
entities: [SameSite cookie, OAuth, widgetly]
causal_relations:
- {source: SameSite=Strict, target: logged out after redirect, type: causes}
links: []
source_episodes: [ep-7d0a7bc0]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-08-05-auth-samesite-lax-oauth-redi.jsonl
  content_hash: f45a882f3937c9c9
  detected_at: '2026-08-05T10:25:00'
  source_memory_ids: [ep-7d0a7bc0]
  proof_count: 1
---

```http
Set-Cookie: sid=...; Path=/; HttpOnly; Secure; SameSite=Lax
```

Debug tip: DevTools > Application > Cookies shows a "blocked due to SameSite" reason on the callback request.
