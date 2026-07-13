import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: compress queue. Multi-select memories and queue them for the /reflect
// consolidate skill — the web app never calls an LLM; it writes a queue group.
test('select memories and queue for compression', async ({ page, request }) => {
  await page.goto('/');
  await page.getByTestId('select-toggle').click();

  // Clicking a card in select mode toggles its selection.
  await page.locator('[data-testid="card"][data-id="beta-cache-redis-decision"]').click();
  await page.locator('[data-testid="card"][data-id="beta-old-inmemory-cache"]').click();

  await expect(page.getByTestId('actionbar')).toBeVisible();
  await expect(page.locator('#ab-count')).toHaveText('2');
  await flowShot(page, 'compress-queue-select');

  await page.getByTestId('compress-btn').click();
  await expect(page.getByTestId('toast')).toContainText('Queued 2');

  // The server persisted the group into the versioned queue file.
  const q = await (await request.get('/api/compress-queue')).json();
  expect(q.version).toBe(1);
  expect(q.groups.length).toBeGreaterThanOrEqual(1);
  const ids = q.groups.flatMap((g) => g.ids);
  expect(ids).toContain('beta-cache-redis-decision');
  expect(ids).toContain('beta-old-inmemory-cache');
});
