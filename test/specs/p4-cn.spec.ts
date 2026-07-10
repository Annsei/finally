import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * A-share (CN) P4 polish E2E (P4_POLISH_CONTRACT.md §5/§6).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as pages-cn.spec.ts.
 * Launched via docker-compose.cn.test.yml (FINALLY_MARKET=cn, LLM_MOCK=true).
 *
 * CN acceptance for the P4 features (contract §5):
 *   (a) the market sentiment label renders in Chinese (冰点/低迷/中性/活跃/
 *       沸腾) and the journal calendar shows a zh month title (yyyy年M月)
 *   (b) the player page renders the equity curve with ¥ figures and
 *       Chinese copy
 *
 * Deeper copy/state pinning (four player-page states, weekday headers,
 * direction-color flips) is jest scope per contract §6.
 */

const CJK = /[一-鿿]/;

/** The five zh sentiment labels (contract §1: i18n 前端渲染). */
const ZH_SENTIMENT = /(冰点|低迷|中性|活跃|沸腾)/;

/** Wait for the live stream (connection-status pattern from the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('A-share (CN) P4 polish', () => {
  test('(a) sentiment label is Chinese and the calendar shows a zh month', async ({
    page,
  }) => {
    await page.goto('/market/');
    await waitForConnected(page);

    // Sentiment dial: score digits + one of the five zh labels.
    const dial = page.getByTestId('market-sentiment');
    await expect(dial).toBeVisible({ timeout: 15_000 });
    await expect(dial).toContainText(/\d/, { timeout: 15_000 });
    await expect(dial).toContainText(ZH_SENTIMENT, { timeout: 15_000 });

    // Journal calendar: the month grid always renders (trades or not), with
    // the month title formatted for profile.locale — zh yields "yyyy年M月".
    await page.goto('/journal/');
    await waitForConnected(page);
    const calendar = page.getByTestId('journal-calendar');
    await expect(calendar).toBeVisible({ timeout: 15_000 });
    await expect(calendar).toContainText(/\d{4}\s*年/, { timeout: 15_000 });
    await expect(calendar).toContainText(CJK);
  });

  test('(b) the player page renders the equity curve with ¥ figures', async ({
    page,
    request,
  }) => {
    // At least one portfolio snapshot must exist for the curve — snapshots
    // are recorded every 30s and after each trade. Poll the API instead of
    // sleeping (T+1 makes a setup round-trip trade impractical under CN).
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio/history');
          if (!res.ok()) return 0;
          const { snapshots } = (await res.json()) as { snapshots: unknown[] };
          return snapshots.length;
        },
        { timeout: 60_000, intervals: [2_000], message: 'waiting for a portfolio snapshot' }
      )
      .toBeGreaterThan(0);

    // Direct deep link, query mode like /symbol?c=… (contract §4).
    await page.goto('/player/?u=default');
    await waitForConnected(page);

    // The equity curve (lightweight-charts, base = CN seed ¥100k) renders.
    const equity = page.getByTestId('player-equity');
    await expect(equity).toBeVisible({ timeout: 15_000 });
    await expect(equity.locator('canvas').first()).toBeVisible({ timeout: 15_000 });

    // ¥-denominated figures and Chinese copy on the page (contract §5:
    // 选手页 ¥ 与中文文案 — formatMoney follows the CN profile).
    await expect(page.locator('body')).toContainText(/¥\s*[\d,]+/, {
      timeout: 15_000,
    });
    await expect(page.locator('body')).toContainText(CJK);
  });
});
