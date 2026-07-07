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
 *
 * A-share spec (cn.spec.ts):
 *   cn.spec.ts asserts the A-share profile and only passes against a
 *   FINALLY_MARKET=cn backend. It lives in the shared specs/ dir, so the DEFAULT
 *   (US) run must skip it — the `e2e` project excludes it via testIgnore. The CN
 *   harness (docker-compose.cn.test.yml) sets CN_E2E=1 on the Playwright
 *   container, which swaps in a single dependency-free `cn` project so
 *   `npx playwright test specs/cn.spec.ts` runs it in isolation (without the
 *   fresh-start US-seed assertions, which would fail under the CN seed).
 *
 *   Note: in Playwright, testIgnore removes a file from discovery even when it
 *   is named explicitly on the command line — so gating the project set on
 *   CN_E2E (rather than relying on the explicit path alone) is what lets the CN
 *   spec run while the default run still skips it.
 */
const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8000';
const CN_E2E = process.env.CN_E2E === '1' || process.env.CN_E2E === 'true';

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
  projects: CN_E2E
    ? [
        // CN harness: run ONLY the A-share spec, with no US-seed dependency.
        {
          name: 'cn',
          testMatch: /cn\.spec\.ts/,
        },
      ]
    : [
        // fresh-start asserts the pristine seeded state ($10,000 cash, default
        // watchlist) so it MUST run before any spec that mutates state.
        {
          name: 'fresh-start',
          testMatch: /fresh-start\.spec\.ts/,
        },
        {
          name: 'e2e',
          // cn.spec.ts is A-share-only — excluded from the default (US) run.
          testMatch: /\.spec\.ts/,
          testIgnore: [/fresh-start\.spec\.ts/, /cn\.spec\.ts/],
          dependencies: ['fresh-start'],
        },
      ],
});
