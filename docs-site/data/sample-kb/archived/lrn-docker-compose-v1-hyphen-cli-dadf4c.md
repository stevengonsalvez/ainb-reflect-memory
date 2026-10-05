---
type: learning
id: lrn-docker-compose-v1-hyphen-cli-dadf4c
created: '2026-04-10'
updated: '2026-04-10'
scope: cross-project
confidence: low
confidence_num: 0.4
learning_type: tooling
discovery_tokens: 18700
title: Use docker-compose (hyphen) binary in CI scripts
tags: [docker, compose, ci]
symptoms:
- "docker-compose: command not found"
key_insight: Runners shipped the standalone docker-compose v1 binary.
problem: Compose step failed on new runner images.
root_cause: Image dropped the v1 binary.
fix: Install docker-compose v1.
rule: Pin runner image versions.
category: devops
entities: [docker compose, Docker, GitHub Actions]
causal_relations: []
links: []
source_episodes: [ep-5e020f5d]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/cross-project/2026-04-10-docker-compose-v1-hyphen-cli.jsonl
  content_hash: 0bdd9235a77868ed
  detected_at: '2026-04-10T13:00:00'
  source_memory_ids: [ep-5e020f5d]
  proof_count: 1
---

Obsolete: Compose v2 ships as `docker compose` (space). Archived because the hyphen binary no longer exists on hosted runners.
