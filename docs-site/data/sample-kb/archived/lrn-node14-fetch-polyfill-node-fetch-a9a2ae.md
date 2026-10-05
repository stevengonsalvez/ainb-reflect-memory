---
type: learning
id: lrn-node14-fetch-polyfill-node-fetch-a9a2ae
created: '2026-04-18'
updated: '2026-04-18'
scope: project-quillbot
confidence: low
confidence_num: 0.42
learning_type: gotcha
discovery_tokens: 16000
title: Polyfill fetch with node-fetch on Node 14
tags: [node, fetch, polyfill]
symptoms:
- "ReferenceError: fetch is not defined"
key_insight: Node 14 has no global fetch.
problem: Scripts using fetch crash.
root_cause: Runtime too old.
fix: Install node-fetch@2 and assign globalThis.fetch.
rule: Upgrade Node instead.
category: build-errors
entities: [Node.js, fetch]
causal_relations: []
links: []
source_episodes: [ep-0a2b2e6e]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/quillbot/2026-04-18-node14-fetch-polyfill-node-f.jsonl
  content_hash: 1136b9cad5ab8a4a
  detected_at: '2026-04-18T16:30:00'
  source_memory_ids: [ep-0a2b2e6e]
  proof_count: 1
---

Obsolete. Node 18+ ships a global `fetch`; the project now runs on Node 22.
