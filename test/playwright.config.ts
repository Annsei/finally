import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for FinAlly E2E tests (PLAN.md §12).
 *
 * The app under test is a single shared instance (one SQLite DB), so specs run
 * serially — trades/watchlist changes in one spec must not race another.
 *
 * BASE_URL:
 *   - docker-compose.test.yml sets BASE_URL=http://app:8000
 *   - locally defaults to http://localhost:8000 (e.g. after scripts/start_mac.sh)
 */
const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8000';

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  forbidOnly: !!process.env.CI,
  // SSE-driven UI: prices tick every ~500ms, EventSource reconnect and the
  // simulator picking up new tickers can take several seconds — be generous.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
    // Desktop-first, data-dense layout — use a wide viewport.
    viewport: { width: 1600, height: 900 },
  },
  projects: [
    // fresh-start asserts the pristine seeded state ($10,000 cash, default
    // watchlist) so it MUST run before any spec that mutates state.
    {
      name: 'fresh-start',
      testMatch: /fresh-start\.spec\.ts/,
    },
    {
      name: 'e2e',
      testMatch: /\.spec\.ts/,
      testIgnore: /fresh-start\.spec\.ts/,
      dependencies: ['fresh-start'],
    },
  ],
});
