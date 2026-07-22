import { test, expect } from '@playwright/test';

/**
 * Fresh start (PLAN.md §12): default watchlist appears, $10k balance shown,
 * prices are streaming, connection indicator reaches "connected".
 *
 * Runs in its own Playwright project so it executes BEFORE any spec that
 * mutates state (see playwright.config.ts) — it asserts the pristine seed.
 */
const DEFAULT_TICKERS = [
  'AAPL',
  'GOOGL',
  'MSFT',
  'AMZN',
  'TSLA',
  'NVDA',
  'META',
  'JPM',
  'V',
  'NFLX',
];

test.describe('fresh start', () => {
  test('default watchlist, $10k balance, streaming prices, connected indicator', async ({
    page,
  }) => {
    await page.goto('/');

    // Connection indicator reaches "connected" (frontend testid contract).
    await expect(page.getByTestId('connection-status')).toHaveAttribute(
      'data-state',
      'connected',
      { timeout: 20_000 }
    );

    // All 10 default tickers render in the watchlist.
    for (const ticker of DEFAULT_TICKERS) {
      await expect(
        page.getByRole('cell', { name: ticker, exact: true }).first()
      ).toBeVisible({ timeout: 15_000 });
    }

    // $10,000.00 visible (cash balance — portfolio total equals it on a fresh DB).
    await expect(page.getByText('$10,000.00').first()).toBeVisible({ timeout: 15_000 });

    // Prices stream: the AAPL watchlist price cell fills in with a number...
    const aaplRow = page
      .getByRole('row')
      .filter({ has: page.getByRole('cell', { name: 'AAPL', exact: true }) })
      .first();
    const priceCell = aaplRow.getByRole('cell').nth(1);
    await expect(priceCell).toHaveText(/\d+\.\d{2}/, { timeout: 15_000 });

    // ...and changes value within ~10s (simulator ticks every ~500ms).
    const initial = ((await priceCell.textContent()) ?? '').trim();
    await expect(priceCell).not.toHaveText(initial, { timeout: 10_000 });
  });
});
