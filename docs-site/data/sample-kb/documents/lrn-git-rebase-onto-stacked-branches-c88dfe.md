---
type: learning
id: lrn-git-rebase-onto-stacked-branches-c88dfe
created: '2026-09-12'
updated: '2026-09-12'
scope: cross-project
confidence: high
confidence_num: 0.88
learning_type: tooling
discovery_tokens: 17600
title: git rebase --onto to move a stacked branch after its parent is squash-merged
tags: [git, rebase, workflow]
symptoms: [PR shows parent branch commits again after squash merge, conflicts replaying already-merged changes]
key_insight: Name the old base explicitly so only the child's own commits are replayed on top of main.
problem: Child PR re-included the parent's commits after the parent was squash-merged.
root_cause: Squash creates a new commit, so git cannot see the parent commits as already upstream.
fix: git rebase --onto main <old-parent-tip> child-branch
rule: Record the parent tip SHA before merging it.
category: devops
entities: [git, git rebase, GitHub Actions]
causal_relations:
- {source: squash merge, target: duplicate commits in child, type: causes}
links: []
source_episodes: [ep-08f67fda]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-09-12-git-rebase-onto-stacked-bran.jsonl
  content_hash: c5773599306559a4
  detected_at: '2026-09-12T11:00:00'
  source_memory_ids: [ep-08f67fda]
  proof_count: 3
---

```bash
git fetch origin
git rebase --onto origin/main feature-parent feature-child
git push --force-with-lease
```

`--force-with-lease` refuses to overwrite commits you have not seen.
