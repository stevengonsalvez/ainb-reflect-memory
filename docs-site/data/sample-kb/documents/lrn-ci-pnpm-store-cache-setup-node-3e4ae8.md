---
type: learning
id: lrn-ci-pnpm-store-cache-setup-node-3e4ae8
created: '2026-08-27'
updated: '2026-09-15'
scope: cross-project
confidence: high
confidence_num: 0.91
learning_type: best-practice
discovery_tokens: 28600
title: Cache the pnpm store with setup-node, not node_modules
tags: [ci, pnpm, cache, github-actions]
symptoms: [restore of node_modules cache takes longer than install]
key_insight: Cache the content-addressed store and let pnpm hard-link; restores are fast and safe across Node versions.
problem: Caching node_modules produced stale native bindings after Node upgrades.
root_cause: node_modules contains compiled artifacts tied to the Node ABI.
fix: "actions/setup-node with cache: pnpm, then pnpm install --frozen-lockfile."
rule: Cache the package-manager store, never node_modules.
category: devops
entities: [pnpm, GitHub Actions, actions/cache, Node.js, widgetly]
causal_relations:
- {source: caching node_modules, target: ABI mismatch, type: causes}
links: [lrn-ci-actions-cache-node-modules-8204a1]
source_episodes: [ep-b46cfe8b]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-08-27-ci-pnpm-store-cache-setup-no.jsonl
  content_hash: 912afadd37e87f5d
  detected_at: '2026-08-27T09:30:00'
  source_memory_ids: [ep-b46cfe8b]
  proof_count: 4
---

```yaml
- uses: pnpm/action-setup@v4
- uses: actions/setup-node@v4
  with:
    node-version: 22
    cache: pnpm
- run: pnpm install --frozen-lockfile
```

Install drops from 70 s cold to 9 s warm. The cache key is derived from `pnpm-lock.yaml` automatically.
