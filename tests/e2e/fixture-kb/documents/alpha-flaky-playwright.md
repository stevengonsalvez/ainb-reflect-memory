---
id: alpha-flaky-playwright
title: "Flaky Playwright test: await network idle"
confidence: high
learning_type: bug-fix
scope: project-alpha
tags: [project-alpha, testing]
created: 2026-06-30
---

# Flaky Playwright test: await network idle

The assertion raced the fetch. Wait for network idle before reading the DOM.
