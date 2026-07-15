import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const SHOTS = join(here, '..', 'test-results', 'screenshots');
mkdirSync(SHOTS, { recursive: true });

// Number of notes in tests/e2e/fixture-kb — keep specs from hardcoding it so
// adding a fixture note doesn't touch five files.
export const FIXTURE_SIZE = 7;

// Save a per-flow screenshot as a durable artifact (separate from Playwright's
// only-on-failure captures).
export async function flowShot(page, name) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}
