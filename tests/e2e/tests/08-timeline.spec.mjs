import { test, expect } from '@playwright/test';
import { flowShot, FIXTURE_SIZE } from './helpers.mjs';

// Browse view: timeline — memories grouped chronologically.
test('timeline lists memories chronologically', async ({ page }) => {
  await page.goto('/#timeline');
  await expect(page.getByTestId('timeline')).toBeVisible();
  await expect(page.getByTestId('timeline-entry')).toHaveCount(FIXTURE_SIZE);
  await flowShot(page, 'timeline');

  await page.getByTestId('timeline-entry').first().click();
  await expect(page.getByTestId('drawer')).toBeVisible();
});
