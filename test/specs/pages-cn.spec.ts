import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * A-share (CN) multi-page E2E (P1_PAGES_CONTRACT.md §8/§9).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as cn.spec.ts.
 * Launched via docker-compose.cn.test.yml (FINALLY_MARKET=cn, LLM_MOCK=true).
 *
 * CN acceptance for the new pages (contract §8):
 *   (a) navigation is Chinese
 *   (b) market page shows the Chinese name column (600519 → 贵州茅台)
 *   (c) symbol page stats show ¥ prices and the 涨停 (limit-up) price row
 */

const CJK = /[一-鿿]/;

/** Wait for the live stream (connection-status pattern from the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('A-share (CN) multi-page workstation', () => {
  test('(a) the navigation is Chinese on all four entries', async ({ page }) => {
    await page.goto('/');
    await waitForConnected(page);

    // All four nav entries render with Chinese labels (zh dict nav.*).
    for (const id of ['nav-desk', 'nav-market', 'nav-journal', 'nav-arena']) {
      const link = page.getByTestId(id);
      await expect(link).toBeVisible({ timeout: 15_000 });
      await expect(link).toHaveText(CJK);
    }
  });

  test('(b) the market grid shows the Chinese name column', async ({ page }) => {
    await page.goto('/market/');
    await waitForConnected(page);

    await expect(page.getByTestId('market-grid')).toBeVisible({ timeout: 15_000 });

    // CN universe row: code 600519 alongside its profile name 贵州茅台.
    const row = page.getByTestId('market-row-600519');
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText('贵州茅台');
  });

  test('(c) the symbol page shows ¥ prices and the limit-up (涨停) row', async ({
    page,
  }) => {
    await page.goto('/symbol/?c=600519');
    await waitForConnected(page);

    // Past the symbol-empty hydration placeholder → day stats render.
    const stats = page.getByTestId('symbol-stats');
    await expect(stats).toBeVisible({ timeout: 15_000 });

    // ¥-denominated stats and the A-share limit-up price row (涨停).
    await expect(stats).toContainText('¥', { timeout: 15_000 });
    await expect(stats).toContainText('涨停');
  });
});
