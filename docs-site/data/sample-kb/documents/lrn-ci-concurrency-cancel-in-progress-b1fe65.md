---
type: learning
id: lrn-ci-concurrency-cancel-in-progress-b1fe65
created: '2026-09-03'
updated: '2026-09-03'
scope: cross-project
confidence: high
confidence_num: 0.87
learning_type: tooling
discovery_tokens: 47400
title: Use concurrency groups to cancel superseded CI runs
tags: [ci, github-actions, cost]
symptoms: [queue of 6 runs for one PR, runner minutes spiking after force-pushes]
key_insight: A concurrency group per ref cancels runs that a newer push has made irrelevant, but never on main.
problem: Rapid pushes queued many identical runs and delayed required checks.
root_cause: Workflows had no concurrency control.
fix: Add concurrency with cancel-in-progress conditional on non-main refs.
rule: Cancel PR runs, never deploy runs.
category: devops
entities: [GitHub Actions, Playwright, Docker]
causal_relations: []
links: []
source_episodes: [ep-cb85b641]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-09-03-ci-concurrency-cancel-in-pro.jsonl
  content_hash: a303eaf7acf0d92d
  detected_at: '2026-09-03T10:50:00'
  source_memory_ids: [ep-cb85b641]
  proof_count: 2
---

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
```

Saved about 35 percent of runner minutes on the widgetly repo.
