---
type: learning
id: lrn-docker-layer-order-deps-first-0de1bc
created: '2026-05-12'
updated: '2026-05-12'
scope: cross-project
confidence: medium
confidence_num: 0.74
learning_type: best-practice
discovery_tokens: 55900
title: "Order Dockerfile layers: dependency manifests before source"
tags: [docker, cache, ci]
symptoms: [npm install runs on every build, docker build never hits cache]
key_insight: Copy only package.json and the lockfile, install, then copy the source so edits do not bust the install layer.
problem: Every commit reinstalled all dependencies in the image build.
root_cause: COPY . . came before the install step, so any file change invalidated it.
fix: Copy manifests first, install, then copy source.
rule: Least-frequently-changing inputs go highest in the Dockerfile.
category: devops
entities: [Docker, pnpm, Node.js, GitHub Actions, widgetly]
causal_relations: []
links: []
source_episodes: [ep-60181f88]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/cross-project/2026-05-12-docker-layer-order-deps-firs.jsonl
  content_hash: 7f51c80e02a67d43
  detected_at: '2026-05-12T09:00:00'
  source_memory_ids: [ep-60181f88]
  proof_count: 2
---

```dockerfile
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
RUN pnpm build
```

Same rule as any language: manifests, install, then source. Pair with a `.dockerignore` that excludes `node_modules` and `.git`.
