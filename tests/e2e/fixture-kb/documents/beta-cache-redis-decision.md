---
id: beta-cache-redis-decision
title: "Use Redis for the session cache"
confidence: high
learning_type: architecture-decision
scope: project-beta
tags: [project-beta, cache]
created: 2026-06-27
---

# Use Redis for the session cache

In-memory cache did not survive restarts. Redis with a 5-minute TTL.
