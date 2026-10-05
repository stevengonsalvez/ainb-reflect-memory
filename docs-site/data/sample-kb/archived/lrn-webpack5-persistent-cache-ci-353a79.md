---
type: learning
id: lrn-webpack5-persistent-cache-ci-353a79
created: '2026-04-15'
updated: '2026-04-15'
scope: project-widgetly
confidence: medium
confidence_num: 0.5
learning_type: performance
discovery_tokens: 59300
title: webpack 5 filesystem cache in CI
tags: [webpack, build, cache]
symptoms: [production build takes 6 minutes]
key_insight: "cache: { type: 'filesystem' } halves rebuild time when the directory is restored."
problem: Slow bundling.
root_cause: No persistent cache.
fix: Enable filesystem cache and restore .cache between runs.
rule: Key the cache on lockfile and config hash.
category: performance
entities: [webpack, GitHub Actions, Node.js]
causal_relations: []
links: []
source_episodes: [ep-70465f15]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-04-15-webpack5-persistent-cache-ci.jsonl
  content_hash: 85874efc0fa6836b
  detected_at: '2026-04-15T09:40:00'
  source_memory_ids: [ep-70465f15]
  proof_count: 1
---

```js
module.exports = { cache: { type: "filesystem", buildDependencies: { config: [__filename] } } };
```

Archived after the migration to Vite made webpack unnecessary.
