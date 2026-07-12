import { test, expect } from '@playwright/test';
import { flowShot } from './helpers.mjs';

// Flow: search. Lexical BM25 ranking; each result card carries a match score
// and the recall-parity score (confidence x recency x tag overlap).
test('search ranks lexical matches and shows scores', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('search').fill('redis session cache');

  const first = page.getByTestId('card').first();
  await expect(first).toContainText('Redis');
  await expect(first.getByTestId('recall-score')).toContainText('match');
  await expect(first.getByTestId('recall-score')).toContainText('recall');
  await expect(page.getByTestId('count')).toContainText('ranked by match');
  await flowShot(page, 'search');
});
