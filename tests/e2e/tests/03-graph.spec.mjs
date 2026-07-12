import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: graph exploration. Two-layer graph (memory + entity nodes) with an
// entity-layer toggle; edge widths encode graphml relation weights.
test('graph exploration with entity toggle', async ({ page }) => {
  await page.goto('/#graph');
  await expect(page.getByTestId('graph-canvas')).toBeVisible();
  await expect(page.getByTestId('graph-count')).toContainText('memories');
  await expect(page.getByTestId('graph-count')).toContainText('entities');
  await flowShot(page, 'graph-with-entities');

  const ents = page.getByTestId('toggle-entities');
  await expect(ents).toBeChecked();
  await ents.uncheck();
  await expect(ents).not.toBeChecked();
  await flowShot(page, 'graph-memories-only');
  await ents.check();
});
