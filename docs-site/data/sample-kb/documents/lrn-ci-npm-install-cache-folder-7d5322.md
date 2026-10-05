---
type: learning
id: lrn-ci-npm-install-cache-folder-7d5322
created: '2026-04-11'
updated: '2026-04-11'
scope: cross-project
confidence: low
confidence_num: 0.4
learning_type: best-practice
discovery_tokens: 60400
title: Cache ~/.npm to speed up npm ci
tags: [ci, npm, cache]
symptoms: [npm ci slow in CI]
key_insight: Persist the npm download cache between runs.
problem: Every CI run re-downloads all tarballs.
root_cause: Ephemeral runners start with an empty ~/.npm.
fix: Cache the ~/.npm directory with actions/cache.
rule: Use npm ci, not npm install, in CI.
category: devops
entities: [npm, actions/cache, GitHub Actions]
causal_relations: []
links: []
source_episodes: [ep-5bc3ac30]
superseded_by: lrn-ci-actions-cache-node-modules-8204a1
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/cross-project/2026-04-11-ci-npm-install-cache-folder.jsonl
  content_hash: a310ef0c07440f19
  detected_at: '2026-04-11T08:00:00'
  source_memory_ids: [ep-5bc3ac30]
  proof_count: 1
---

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.npm
    key: npm-${{ hashFiles('package-lock.json') }}
```

Saves downloads but not the extraction and link step, so gains were modest (about 25 percent).
