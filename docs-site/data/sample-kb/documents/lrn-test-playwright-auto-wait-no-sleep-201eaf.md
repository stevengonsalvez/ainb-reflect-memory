---
type: learning
id: lrn-test-playwright-auto-wait-no-sleep-201eaf
created: '2026-06-05'
updated: '2026-06-05'
scope: project-widgetly
confidence: high
confidence_num: 0.89
learning_type: best-practice
discovery_tokens: 10000
title: "Playwright: rely on auto-waiting locators, never waitForTimeout"
tags: [testing, playwright, flaky, e2e]
symptoms: [test passes locally and fails on CI runners, Timeout 30000ms exceeded waiting for selector]
key_insight: Web-first assertions retry until the condition holds; fixed sleeps either waste time or flake.
problem: e2e suite had a 7 percent flake rate on shared CI runners.
root_cause: Tests used page.waitForTimeout(2000) instead of waiting for state.
fix: Replace sleeps with expect(locator).toBeVisible() style assertions.
rule: No waitForTimeout in committed tests.
category: testing
entities: [Playwright, flaky test, widgetly, GitHub Actions]
causal_relations:
- {source: fixed sleep, target: flaky test, type: causes}
links: []
source_episodes: [ep-5483eacc]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/widgetly/2026-06-05-test-playwright-auto-wait-no.jsonl
  content_hash: 27caedfa7d0f0017
  detected_at: '2026-06-05T14:00:00'
  source_memory_ids: [ep-5483eacc]
  proof_count: 3
---

```ts
// before
await page.click("text=Save");
await page.waitForTimeout(2000);
// after
await page.getByRole("button", { name: "Save" }).click();
await expect(page.getByText("Saved")).toBeVisible();
```

Add `retries: 1` plus `trace: "on-first-retry"` so the remaining flakes leave evidence.
