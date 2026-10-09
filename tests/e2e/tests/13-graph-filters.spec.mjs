import { test, expect } from '@playwright/test';
import { flowShot, FIXTURE_SIZE } from './helpers.mjs';

// Facet filters, search and entity click-through must narrow EVERY view
// (list, timeline, graph) to the same set of memories.
test('facet filter narrows list, timeline and graph together', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('facet-tag-project-alpha').click();
  await expect(page.getByTestId('card')).toHaveCount(3);
  await expect(page.getByTestId('fchip-tag')).toBeVisible();

  await page.getByTestId('tab-timeline').click();
  await expect(page.getByTestId('timeline-entry')).toHaveCount(3);

  await page.getByTestId('tab-graph').click();
  await expect(page.getByTestId('graph-count')).toContainText(`3 of ${FIXTURE_SIZE} memories (filtered)`);
  await flowShot(page, 'graph-filtered');

  // clearing from inside the graph HUD restores everything
  await page.getByTestId('graph-clear-filters').click();
  await expect(page.getByTestId('graph-count')).not.toContainText('filtered');
  await expect(page.getByTestId('graph-count')).toContainText(`${FIXTURE_SIZE} memories`);
});

test('search keeps you on the graph and filters it', async ({ page }) => {
  await page.goto('/#graph');
  await expect(page.getByTestId('graph-count')).toContainText('memories');
  await page.getByTestId('search').fill('alpha');
  await expect(page.getByTestId('graph-count')).toContainText('filtered');
  await expect(page.getByTestId('graph-canvas')).toBeVisible();
  await page.getByTestId('graph-fchip-search').getByRole('button').click();
  await expect(page.getByTestId('graph-count')).not.toContainText('filtered');
});

test('a failed graph load shows an error and the next visit retries', async ({ page }) => {
  let fail = true;
  await page.route('**/api/graph', route =>
    fail ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' })
         : route.continue());
  await page.goto('/#graph');
  await expect(page.getByTestId('graph-count')).toContainText('graph failed');

  fail = false;
  await page.getByTestId('tab-memories').click();
  await page.getByTestId('tab-graph').click();
  await expect(page.getByTestId('graph-count')).toContainText(`${FIXTURE_SIZE} memories`);
});

test('clicking an entity filters every view to its memories', async ({ page }) => {
  await page.goto('/#graph');
  await expect(page.getByTestId('graph-count')).toContainText('memories');
  // locate an entity node in canvas coordinates and click it for real
  const pos = await page.evaluate(() => {
    const n = GRAPH.nodes.find(n => n.kind === 'entity' && n.nbrs.some(x => x.kind === 'memory'));
    const r = canvas.getBoundingClientRect();
    return { x: r.left + n.x * view.k + view.x, y: r.top + n.y * view.k + view.y };
  });
  await page.mouse.move(pos.x, pos.y);
  await page.mouse.down();
  await page.mouse.up();
  await expect(page.getByTestId('graph-fchip-entity')).toBeVisible();
  await expect(page.getByTestId('graph-count')).toContainText('filtered');
  await page.getByTestId('tab-memories').click();
  await expect(page.getByTestId('fchip-entity')).toBeVisible();
  await expect(page.getByTestId('card').first()).toBeVisible();
});

test('long lists render in chunks behind a Show more button', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => {
    for (let i = 0; i < 250; i++) MEMS.push({
      id: 'bulk-' + i, title: 'bulk ' + i, confidence: 'high', type: 't', scope: 's',
      tags: [], date: '2026-01-01', superseded_by: null, entity_names: [],
      entity_count: 0, word_count: 1, browse_score: 0,
    });
    renderList();
  });
  await expect(page.getByTestId('card')).toHaveCount(200);
  await page.getByTestId('show-more').click();
  await expect(page.getByTestId('card')).toHaveCount(FIXTURE_SIZE + 250);
});

test('provenance mapping renders as fields, not [object Object]', async ({ page }) => {
  await page.goto('/');
  const html = await page.evaluate(() => fmtProv({ source_tool: 'claude', n: 2 }));
  expect(html).toContain('source_tool');
  expect(html).not.toContain('object Object');
});
