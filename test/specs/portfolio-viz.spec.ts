import { test, expect } from '@playwright/test';
import { trade, flattenPosition } from './helpers';

/**
 * Portfolio visualizations (PLAN.md §12): after a buy, the heatmap renders a
 * tile for the position and the P&L chart is present with data.
 */
const TICKER = 'MSFT';
const QTY = 4;

test.describe('portfolio visualizations', () => {
  test('heatmap tile renders for a position and the P&L chart has data', async ({
    page,
    request,
  }) => {
    // Seed a position via the API so the visualizations have data.
    const res = await trade(request, TICKER, 'buy', QTY);
    expect(res.ok()).toBeTruthy();

    try {
      await page.goto('/');

      // Heatmap tile: the deepest <div> containing the ticker, a $ value and a
      // % change — i.e. the treemap tile. (The positions table is <td>-based
      // and the watchlist shows prices without a $ sign, so they don't match.)
      const tile = page
        .locator('div')
        .filter({ has: page.getByText(TICKER, { exact: true }) })
        .filter({ hasText: /\$\d/ })
        .filter({ hasText: '%' })
        .last();
      await expect(tile).toBeVisible({ timeout: 15_000 });
      await expect(tile).toContainText(TICKER);

      // P&L chart is canvas-based (lightweight-charts) — a canvas must render.
      await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 });

      // The trade recorded a portfolio snapshot, so the P&L series has data points.
      const historyRes = await request.get('/api/portfolio/history');
      expect(historyRes.ok()).toBeTruthy();
      const history = (await historyRes.json()) as {
        snapshots: { total_value: number; recorded_at: string }[];
      };
      expect(history.snapshots.length).toBeGreaterThan(0);
    } finally {
      // Clean up the seeded position even if an assertion failed.
      await flattenPosition(request, TICKER);
    }
  });
});
