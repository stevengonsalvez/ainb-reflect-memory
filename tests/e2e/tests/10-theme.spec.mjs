import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Impeccable UI must work in light AND dark.
test('light and dark themes both render', async ({ page }) => {
  await page.goto('/');
  await flowShot(page, 'theme-light');

  const html = page.locator('html');
  const before = await html.getAttribute('data-theme');
  await page.getByTestId('theme-toggle').click();
  const after = await html.getAttribute('data-theme');
  expect(after).not.toBe(before);
  await flowShot(page, 'theme-dark');
});
