import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const SHOTS = join(here, '..', 'test-results', 'screenshots');
mkdirSync(SHOTS, { recursive: true });

// Save a per-flow screenshot as a durable artifact (separate from Playwright's
// only-on-failure captures).
export async function flowShot(page, name) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}
