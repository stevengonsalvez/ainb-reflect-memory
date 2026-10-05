---
type: learning
id: lrn-react-effect-cleanup-ignore-flag-4ad6bf
created: '2026-06-26'
updated: '2026-06-26'
scope: project-widgetly
confidence: low
confidence_num: 0.46
learning_type: best-practice
discovery_tokens: 44600
title: Guard async effect results with an ignore flag in cleanup
tags: [react, useeffect, fetch, race-condition]
symptoms: [setState on unmounted component warning, old response replaces new one]
key_insight: A local `ignore` boolean set in the cleanup stops late responses from calling setState.
problem: Stale async results overwrote fresh state when props changed quickly.
root_cause: No way to discard a response once the effect was superseded.
fix: let ignore = false; set it true in the cleanup; check before setState.
rule: Check the flag before every setState after an await.
category: debugging-sessions
entities: [useEffect, fetch, race condition, React, widgetly]
causal_relations: []
links: []
source_episodes: [ep-99340e91]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/widgetly/2026-06-26-react-effect-cleanup-ignore-.jsonl
  content_hash: d5cfae146e1b94fc
  detected_at: '2026-06-26T09:50:00'
  source_memory_ids: [ep-99340e91]
  proof_count: 1
---

```tsx
useEffect(() => {
  let ignore = false;
  load(id).then(data => { if (!ignore) setData(data); });
  return () => { ignore = true; };
}, [id]);
```

Prefer AbortController when the request itself can be cancelled; the flag only drops the result.
