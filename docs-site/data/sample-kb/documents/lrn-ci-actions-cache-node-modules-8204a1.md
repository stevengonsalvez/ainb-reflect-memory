---
type: learning
id: lrn-ci-actions-cache-node-modules-8204a1
created: '2026-05-02'
updated: '2026-05-02'
scope: cross-project
confidence: medium
confidence_num: 0.5
learning_type: best-practice
discovery_tokens: 13200
title: Cache node_modules with actions/cache keyed on the lockfile
tags: [ci, cache, github-actions, node]
symptoms: [npm ci takes 3 minutes on every run]
key_insight: Restore node_modules directly when package-lock.json has not changed.
problem: npm ci dominated CI wall time.
root_cause: No dependency caching configured.
fix: actions/cache on node_modules with a hashFiles(lockfile) key.
rule: Key the cache on the lockfile hash.
category: devops
entities: [actions/cache, GitHub Actions, Node.js, npm]
causal_relations: []
links: []
source_episodes: [ep-8f52bf59]
superseded_by: lrn-ci-pnpm-store-cache-setup-node-3e4ae8
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-05-02-ci-actions-cache-node-module.jsonl
  content_hash: e6b5a25872112c72
  detected_at: '2026-05-02T11:15:00'
  source_memory_ids: [ep-8f52bf59]
  proof_count: 1
---

```yaml
- uses: actions/cache@v4
  with:
    path: node_modules
    key: nm-${{ hashFiles('package-lock.json') }}
```

Superseded after a Node 20 to 22 upgrade restored binaries built for the old ABI.
