import { test, expect } from '@playwright/test';
import { getPortfolio, flattenPosition } from './helpers';

/**
 * Manual trading via the trade bar (PLAN.md §12):
 *   buy  → cash decreases, position row appears
 *   sell → position disappears, cash increases
 *
 * Trade bar selectors come from frontend/src/components/TradeBar.tsx:
 * inputs labeled "Ticker" / "Qty", buttons "Buy" / "Sell".
 */
const TICKER = 'NVDA';
const QTY = 2;

test.describe('manual trading', () => {
  test.beforeEach(async ({ request }) => {
    // Start with no NVDA position (e.g. left over from a retried run).
    await flattenPosition(request, TICKER);
  });

  test('buy then sell all via the trade bar', async ({ page, request }) => {
    await page.goto('/');
    // Live prices must be flowing for a market order to fill.
    await expect(page.getByTestId('connection-status')).toHaveAttribute(
      'data-state',
      'connected',
      { timeout: 20_000 }
    );

    const before = await getPortfolio(request);

    // --- Buy ---
    await page.getByLabel('Ticker', { exact: true }).fill(TICKER);
    await page.getByLabel('Qty', { exact: true }).fill(String(QTY));
    await page.getByRole('button', { name: 'Buy', exact: true }).click();

    // Position row appears in the positions table. Uses the frontend testid
    // contract (data-testid="position-row-<TICKER>") — role-based table
    // locators are unreliable here: lightweight-charts renders ~12 internal
    // layout <table>s, and th→columnheader role inference varies across
    // Playwright versions.
    const positionRow = page.getByTestId(`position-row-${TICKER}`);
    await expect(positionRow).toBeVisible({ timeout: 15_000 });

    // Cash decreased.
    await expect
      .poll(async () => (await getPortfolio(request)).cash, { timeout: 15_000 })
      .toBeLessThan(before.cash);

    const afterBuy = await getPortfolio(request);
    const position = afterBuy.positions.find((p) => p.ticker === TICKER);
    expect(position?.quantity).toBeCloseTo(QTY, 5);

    // --- Sell everything ---
    await page.getByLabel('Ticker', { exact: true }).fill(TICKER);
    await page.getByLabel('Qty', { exact: true }).fill(String(QTY));
    await page.getByRole('button', { name: 'Sell', exact: true }).click();

    // Position row disappears and cash increases again.
    await expect(positionRow).toHaveCount(0, { timeout: 15_000 });
    await expect
      .poll(async () => (await getPortfolio(request)).cash, { timeout: 15_000 })
      .toBeGreaterThan(afterBuy.cash);

    const after = await getPortfolio(request);
    expect(after.positions.find((p) => p.ticker === TICKER)).toBeUndefined();
  });
});
