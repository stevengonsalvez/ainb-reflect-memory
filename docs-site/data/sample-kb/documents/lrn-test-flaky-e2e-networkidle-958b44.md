---
type: learning
id: lrn-test-flaky-e2e-networkidle-958b44
created: '2026-06-09'
updated: '2026-06-09'
scope: project-widgetly
confidence: low
confidence_num: 0.44
learning_type: gotcha
discovery_tokens: 61900
title: "Flaky e2e: waitForLoadState('networkidle') hangs on polling pages"
tags: [testing, playwright, flaky, e2e]
symptoms:
- "page.waitForLoadState: Timeout 30000ms exceeded"
- dashboard test only fails on CI
key_insight: networkidle never fires when the page polls or holds a websocket; wait for a concrete element instead.
problem: Dashboard test hung until timeout because the page polls /metrics every 5 seconds.
root_cause: networkidle requires 500 ms with no requests, which polling prevents.
fix: Wait for a locator that proves the data rendered.
rule: Avoid networkidle; Playwright docs discourage it too.
category: testing
entities: [Playwright, networkidle, flaky test, widgetly]
causal_relations:
- {source: polling page, target: networkidle timeout, type: causes}
links: []
source_episodes: [ep-035d480f]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/widgetly/2026-06-09-test-flaky-e2e-networkidle.jsonl
  content_hash: 504bf15d3ca0bdb0
  detected_at: '2026-06-09T09:35:00'
  source_memory_ids: [ep-035d480f]
  proof_count: 1
---

```ts
// hangs
await page.goto("/dashboard", { waitUntil: "networkidle" });
// stable
await page.goto("/dashboard");
await expect(page.getByTestId("kpi-revenue")).toHaveText(/\$\d/);
```

Rule of thumb: if the page has polling, SSE or a websocket, there is no idle moment to wait for.
Prefer a web-first assertion on the element that proves the data arrived.
