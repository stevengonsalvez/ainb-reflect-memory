import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.PORT || '8961';
const BASE = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './tests',
  // The suite mutates a single shared server-side KB copy, so run serially.
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: 'test-results/results.json' }],
  ],
  outputDir: 'test-results/output',
  use: {
    baseURL: BASE,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'node serve-fixture.mjs',
    url: `${BASE}/api/stats`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { PORT },
  },
});
