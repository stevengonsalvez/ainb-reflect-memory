---
type: learning
id: lrn-docker-buildkit-cache-mounts-fecb00
created: '2026-05-20'
updated: '2026-05-20'
scope: cross-project
confidence: medium
confidence_num: 0.55
learning_type: performance
discovery_tokens: 25900
title: BuildKit cache mounts speed up package installs and survive layer busts
tags: [docker, buildkit, cache, perf]
symptoms: [image build re-downloads dependencies after any lockfile change]
key_insight: RUN --mount=type=cache keeps the package-manager cache outside the image so even a layer bust is fast.
problem: Lockfile changes still re-downloaded hundreds of MB of packages.
root_cause: Layer cache is all-or-nothing; the package store was thrown away with the layer.
fix: Mount a persistent cache directory for the package manager during RUN.
rule: Use cache mounts for cargo registry, pnpm store and pip cache.
category: devops
entities: [BuildKit, Docker, cargo, pnpm, GitHub Actions]
causal_relations: []
links: []
source_episodes: [ep-9904f2a0]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/cross-project/2026-05-20-docker-buildkit-cache-mounts.jsonl
  content_hash: 48aec3578eeac75f
  detected_at: '2026-05-20T14:10:00'
  source_memory_ids: [ep-9904f2a0]
  proof_count: 1
---

```dockerfile
# syntax=docker/dockerfile:1.7
RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/app/target \
    cargo build --release
```

On CI the mount cache is local to the runner; export it with `cache-to: type=gha,mode=max` or it is lost between jobs.
