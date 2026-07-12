---
id: alpha-db-migration-order
title: "Run Flyway migration before the seed step"
confidence: medium
learning_type: tooling-setup
scope: project-alpha
tags: [project-alpha, database]
created: 2026-06-24
---

# Run Flyway migration before the seed step

The seed references tables the migration creates; ordering is load-bearing.
