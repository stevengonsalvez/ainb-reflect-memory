---
type: learning
id: lrn-yarn-v1-resolutions-pin-1a416c
created: '2026-04-08'
updated: '2026-04-08'
scope: project-widgetly
confidence: low
confidence_num: 0.45
learning_type: tooling
discovery_tokens: 46900
title: Pin transitive deps with yarn v1 resolutions
tags: [yarn, node, dependencies]
symptoms: [vulnerable transitive dependency]
key_insight: resolutions in package.json forces a version for nested packages.
problem: Audit flagged a deep dependency.
root_cause: Transitive version range too loose.
fix: Add a resolutions entry.
rule: Run yarn audit after pinning.
category: devops
entities: [yarn, Node.js, widgetly]
causal_relations: []
links: []
source_episodes: [ep-fe76f21e]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-04-08-yarn-v1-resolutions-pin.jsonl
  content_hash: 8a8a6a2df4818c7b
  detected_at: '2026-04-08T10:00:00'
  source_memory_ids: [ep-fe76f21e]
  proof_count: 1
---

```json
{ "resolutions": { "minimist": "^1.2.8" } }
```

Archived: widgetly migrated to pnpm, which uses `pnpm.overrides` instead.
