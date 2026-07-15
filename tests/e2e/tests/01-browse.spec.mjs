import { test, expect } from '@playwright/test';
import { flowShot, FIXTURE_SIZE } from './helpers.mjs';

// Flow: browse-by-project. Projects are expressed as tags on the notes, so the
// tag facet is the project grouping.
test('browse by project via tag facet', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('count')).toContainText(`${FIXTURE_SIZE} memories`);
  await expect(page.getByTestId('card')).toHaveCount(FIXTURE_SIZE);

  await page.getByTestId('facet-tag-project-alpha').click();
  await expect(page.getByTestId('card')).toHaveCount(3);
  await expect(page.getByTestId('count')).toContainText('3 memories');
  await flowShot(page, 'browse-by-project');

  // clearing the facet restores the full set
  await page.getByTestId('facet-tag-project-alpha').click();
  await expect(page.getByTestId('card')).toHaveCount(FIXTURE_SIZE);
});
