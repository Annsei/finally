import { test, expect } from '@playwright/test';
import { removeFromWatchlist } from './helpers';

/**
 * Watchlist management (PLAN.md §12): add a ticker, see it pick up a live
 * price, then remove it.
 *
 * Uses the frontend testid contract:
 *   [data-testid="watchlist-add-input"], [data-testid="watchlist-add-button"],
 *   [data-testid="watchlist-remove-<TICKER>"]
 */
const TICKER = 'PYPL';

test.describe('watchlist management', () => {
  test.beforeEach(async ({ request }) => {
    // Defensive: PYPL may linger from the chat spec's mock watchlist add.
    await removeFromWatchlist(request, TICKER);
  });

  test('add a ticker, see a live price, then remove it', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('connection-status')).toHaveAttribute(
      'data-state',
      'connected',
      { timeout: 20_000 }
    );

    // Add PYPL via the watchlist controls.
    await page.getByTestId('watchlist-add-input').fill(TICKER);
    await page.getByTestId('watchlist-add-button').click();

    // Row appears...
    await expect(
      page.getByRole('cell', { name: TICKER, exact: true }).first()
    ).toBeVisible({ timeout: 10_000 });

    // ...and eventually shows a streaming price once the market data source
    // picks up the new ticker.
    const row = page
      .getByRole('row')
      .filter({ has: page.getByRole('cell', { name: TICKER, exact: true }) })
      .first();
    await expect(row.getByRole('cell').nth(1)).toHaveText(/\d+\.\d{2}/, {
      timeout: 30_000,
    });

    // Remove it again — hover the row first (the remove button fades in on
    // hover), then the row disappears.
    await row.hover();
    await page.getByTestId(`watchlist-remove-${TICKER}`).click();
    await expect(page.getByRole('cell', { name: TICKER, exact: true })).toHaveCount(0, {
      timeout: 10_000,
    });
  });
});
