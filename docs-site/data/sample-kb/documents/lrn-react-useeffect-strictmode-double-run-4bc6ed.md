---
type: learning
id: lrn-react-useeffect-strictmode-double-run-4bc6ed
created: '2026-05-16'
updated: '2026-05-16'
scope: project-widgetly
confidence: medium
confidence_num: 0.6
learning_type: gotcha
discovery_tokens: 26800
title: useEffect runs twice in dev StrictMode; make effects idempotent
tags: [react, useeffect, strictmode, frontend]
symptoms: [analytics event fired twice in dev, websocket connects twice]
key_insight: StrictMode mounts, unmounts and remounts to flush out missing cleanup; do not suppress it, fix the effect.
problem: Duplicate network calls and subscriptions in development only.
root_cause: Effect had no cleanup so the simulated unmount left the first subscription alive.
fix: Return a cleanup function that undoes the setup.
rule: If an effect has setup, it needs the matching teardown.
category: debugging-sessions
entities: [useEffect, StrictMode, React, widgetly]
causal_relations:
- {source: missing cleanup, target: duplicate subscription, type: causes}
links: []
source_episodes: [ep-ebf4a574]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-05-16-react-useeffect-strictmode-d.jsonl
  content_hash: b8a5e3badbbc309a
  detected_at: '2026-05-16T10:10:00'
  source_memory_ids: [ep-ebf4a574]
  proof_count: 2
---

```tsx
useEffect(() => {
  const ws = new WebSocket(url);
  ws.onmessage = onMessage;
  return () => ws.close();       // StrictMode relies on this
}, [url]);
```

Production runs the effect once; do not "fix" it with a ref guard, that hides real leaks.
