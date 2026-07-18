import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: the tiny markdown renderer (`md()`) supports flat bullet lists inside
// a note body. Open a fixture note whose body has a `- ` list and confirm it
// renders as a real <ul>/<li>, not raw dashes.
test('note body renders a bulleted list', async ({ page }) => {
  await page.goto('/');

  await page.locator('[data-testid="card"][data-id="alpha-db-migration-order"]').click();
  await expect(page.getByTestId('drawer')).toBeVisible();

  const list = page.locator('#d-body .md ul');
  await expect(list).toBeVisible();
  await expect(list.locator('li')).toHaveCount(3);
  await flowShot(page, 'note-body-lists');
});
