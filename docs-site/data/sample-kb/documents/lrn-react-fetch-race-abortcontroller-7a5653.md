---
type: learning
id: lrn-react-fetch-race-abortcontroller-7a5653
created: '2026-06-24'
updated: '2026-06-24'
scope: project-widgetly
confidence: high
confidence_num: 0.84
learning_type: bug-fix
discovery_tokens: 10300
title: Abort stale fetches in useEffect to avoid out-of-order results
tags: [react, useeffect, fetch, race-condition]
symptoms: [search results flicker to an older query, table shows data for the previous filter]
key_insight: Responses can arrive out of order; cancel the previous request when the dependency changes.
problem: Typing quickly showed results for a stale query.
root_cause: Slower earlier request resolved after the later one and overwrote state.
fix: Create an AbortController per effect run and abort it in the cleanup.
rule: Every fetch in an effect gets a signal.
category: debugging-sessions
entities: [AbortController, useEffect, fetch, race condition, React, widgetly]
causal_relations:
- {source: out-of-order response, target: stale UI, type: causes}
links: []
source_episodes: [ep-d894762d]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-06-24-react-fetch-race-abortcontro.jsonl
  content_hash: 841f93ce5b3cd1f8
  detected_at: '2026-06-24T11:45:00'
  source_memory_ids: [ep-d894762d]
  proof_count: 3
---

```tsx
useEffect(() => {
  const ac = new AbortController();
  fetch(`/api/search?q=${q}`, { signal: ac.signal })
    .then(r => r.json())
    .then(setResults)
    .catch(e => { if (e.name !== "AbortError") throw e; });
  return () => ac.abort();
}, [q]);
```
