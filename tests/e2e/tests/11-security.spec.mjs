import { test, expect } from '@playwright/test';

// Regression: the adversarial review found a stored-XSS hole (facet values
// rendered raw) and no CSRF defense on mutations. These pin both fixes.

test('facet values are HTML-escaped and do not execute', async ({ page }) => {
  await page.goto('/');

  const { escaped } = await page.evaluate(() => {
    // Inject a memory whose tag carries an HTML/JS payload, then re-render.
    MEMS.push({
      id: 'xss-probe', title: 'probe', confidence: 'high', type: 't', scope: 's',
      tags: ['<img src=x onerror=window.__xss=1>'], date: '2026-01-01',
      superseded_by: null, entity_names: [], entity_count: 0, word_count: 0,
      browse_score: 0,
    });
    renderFacets();
    const html = document.getElementById('facets').innerHTML;
    return { escaped: html.includes('&lt;img') && !html.includes('<img src=x') };
  });

  expect(escaped).toBe(true);
  // the payload must not have executed during innerHTML assignment
  expect(await page.evaluate(() => window.__xss === 1)).toBe(false);
});

test('mutation POST without X-Reflect header is rejected', async ({ page }) => {
  // page.request does NOT send the SPA's X-Reflect header, standing in for a
  // cross-origin drive-by POST. The server must refuse it.
  const res = await page.request.post('/api/memories/orphan-untagged-note/archive');
  expect(res.status()).toBe(403);
});
