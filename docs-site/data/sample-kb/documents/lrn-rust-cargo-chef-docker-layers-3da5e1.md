---
type: learning
id: lrn-rust-cargo-chef-docker-layers-3da5e1
created: '2026-05-09'
updated: '2026-07-14'
scope: project-acme-api
confidence: medium
confidence_num: 0.66
learning_type: best-practice
discovery_tokens: 8900
title: cargo-chef keeps Rust dependency layers cached in Docker
tags: [rust, docker, ci, cache]
symptoms: [docker build recompiles every crate on each commit, CI image build takes 14 minutes]
key_insight: Plan dependencies with cargo chef prepare so the compile layer only invalidates when Cargo.lock changes.
problem: Any source edit invalidated the cargo build layer and recompiled 300 crates.
root_cause: COPY . . before cargo build placed source above the dependency compile step.
fix: "Three-stage Dockerfile: chef prepare, chef cook, then build the app."
rule: Dependencies first, source last, in every language.
category: devops
entities: [cargo-chef, Docker, Rust, cargo, GitHub Actions, acme-api]
causal_relations:
- {source: COPY . . before build, target: full recompile, type: causes}
links: []
source_episodes: [ep-3f808804]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-05-09-rust-cargo-chef-docker-layer.jsonl
  content_hash: 4fe4ff80087812a3
  detected_at: '2026-05-09T08:20:00'
  source_memory_ids: [ep-3f808804]
  proof_count: 2
---

## Solution

```dockerfile
FROM lukemathwalker/cargo-chef:latest-rust-1.83 AS chef
WORKDIR /app

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json   # cached layer
COPY . .
RUN cargo build --release --bin acme-api
```

Cold build: 14 min. Warm build after a source-only change: 90 s.
