import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: weightages. All four surfaced — browse score on cards, editable
// confidence in the drawer, graph edge weights (visual, covered in 03), and
// recall-usage stats. Net-neutral: it restores the edited confidence so it
// doesn't couple later specs to its mutation.
test('surfaces and edits the four weightages', async ({ page }) => {
  await page.goto('/');

  // 1. computed browse score on every card
  await expect(page.getByTestId('card').first().getByTestId('browse-score'))
    .toContainText('browse');

  // 2. editable confidence
  await page.locator('[data-testid="card"][data-id="alpha-db-migration-order"]').click();
  await expect(page.getByTestId('detail-confidence')).toHaveText('medium');
  await page.getByTestId('conf-high').click();
  await expect(page.getByTestId('toast')).toContainText('Confidence');
  await expect(page.getByTestId('detail-confidence')).toHaveText('high');
  await flowShot(page, 'weightages-confidence');

  // restore the original value so the shared fixture ends where it started
  await page.getByTestId('conf-medium').click();
  await expect(page.getByTestId('detail-confidence')).toHaveText('medium');
  await page.keyboard.press('Escape');

  // 4. recall usage stats (engine ops) in the stats view
  await page.getByTestId('tab-stats').click();
  await expect(page.getByTestId('stat-ops')).toContainText('recall_search');
});
