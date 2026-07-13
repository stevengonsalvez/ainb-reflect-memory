import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Browse view: superseded chains — newest tip to oldest ancestor.
test('superseded chains render newest to oldest', async ({ page }) => {
  await page.goto('/#superseded');
  await expect(page.getByTestId('superseded')).toBeVisible();

  const chains = page.getByTestId('chain');
  await expect(chains).toHaveCount(1);
  await expect(chains.first()).toContainText('Redis');       // newest tip
  await expect(chains.first()).toContainText('superseded');  // old note title
  await flowShot(page, 'superseded-chains');
});
