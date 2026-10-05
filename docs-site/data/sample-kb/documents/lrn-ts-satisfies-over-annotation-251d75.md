---
type: learning
id: lrn-ts-satisfies-over-annotation-251d75
created: '2026-07-16'
updated: '2026-07-16'
scope: project-widgetly
confidence: medium
confidence_num: 0.58
learning_type: best-practice
discovery_tokens: 9000
title: Use `satisfies` to check an object literal without widening its type
tags: [typescript, types, frontend]
symptoms: [config lookup returns string | number instead of literal keys]
key_insight: "`: Record<string, Route>` loses key literals; `satisfies` keeps them while still validating shape."
problem: Route table lost its literal keys, so typos in route names compiled fine.
root_cause: Type annotation widens the inferred type to the annotation.
fix: Replace the annotation with `satisfies Record<string, Route>`.
rule: Annotate function boundaries, satisfy object literals.
category: architecture
entities: [TypeScript, widgetly, Vite]
causal_relations: []
links: []
source_episodes: [ep-288575b0]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-07-16-ts-satisfies-over-annotation.jsonl
  content_hash: d23ef4a9d1a36cca
  detected_at: '2026-07-16T13:30:00'
  source_memory_ids: [ep-288575b0]
  proof_count: 1
---

```ts
const routes = {
  home: { path: "/", auth: false },
  billing: { path: "/billing", auth: true },
} satisfies Record<string, Route>;

routes.billing.path;   // "/billing" (literal), typos on keys now error
```
