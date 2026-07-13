import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: archive/restore. Soft-archive removes a memory from the browsable set
// and from the file-based recall corpus immediately; the cached graph index
// still returns it until `reflect reindex`. The Archived tab restores it.
// Net-neutral so the shared fixture ends where it started.
test('archive a memory then restore it', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('card')).toHaveCount(7);

  await page.locator('[data-testid="card"][data-id="orphan-untagged-note"]').click();
  await expect(page.getByTestId('drawer')).toBeVisible();
  await flowShot(page, 'archive-detail');

  await page.getByTestId('archive-btn').click();
  await expect(page.getByTestId('toast')).toContainText('Archived');
  await expect(page.getByTestId('card')).toHaveCount(6);

  await page.getByTestId('tab-archived').click();
  const row = page.locator('[data-testid="arch-row"][data-id="orphan-untagged-note"]');
  await expect(row).toBeVisible();
  await flowShot(page, 'archived-view');

  await row.getByTestId('restore-btn').click();
  await expect(page.getByTestId('toast')).toContainText('Restored');

  await page.getByTestId('tab-memories').click();
  await expect(page.getByTestId('card')).toHaveCount(7);
});
