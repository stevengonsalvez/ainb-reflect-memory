import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: prune-via-stats. The stats view surfaces the confidence distribution
// (the unknown-confidence bucket = prune candidates) and engine-op usage
// counts (the recall-usage weightage).
test('stats surface prune candidates and usage', async ({ page }) => {
  await page.goto('/#stats');
  await expect(page.getByTestId('stat-count')).toHaveText('7');
  await expect(page.getByTestId('stat-confidence')).toContainText('unknown');
  await expect(page.getByTestId('stat-ops')).toContainText('search');
  await flowShot(page, 'prune-via-stats');
});
