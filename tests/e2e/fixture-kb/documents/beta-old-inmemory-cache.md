---
id: beta-old-inmemory-cache
title: "In-memory session cache (superseded)"
confidence: low
learning_type: architecture-decision
scope: project-beta
tags: [project-beta, cache]
superseded_by: beta-cache-redis-decision
created: 2026-05-10
---

# In-memory session cache (superseded)

Original approach kept sessions in process memory. Lost on every deploy.
