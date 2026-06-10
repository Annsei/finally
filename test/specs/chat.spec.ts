import { test, expect } from '@playwright/test';
import { getPortfolio, flattenPosition, removeFromWatchlist } from './helpers';

/**
 * AI chat with LLM_MOCK=true (PLAN.md §12).
 *
 * The mock path (backend/app/routes/chat.py) deterministically replies:
 *   "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
 * and auto-executes trades=[AAPL buy 5] and watchlist_changes=[PYPL add],
 * which the frontend renders inline as action badges
 * ("Bought 5 AAPL @ $…", "Added PYPL").
 */
const MOCK_REPLY =
  "I've added PYPL to your watchlist and bought 5 shares of AAPL for you.";

test.describe('AI chat (mock mode)', () => {
  test.beforeEach(async ({ request }) => {
    // The mock always buys 5 AAPL and adds PYPL — start from a clean slate so
    // retries and later specs see a predictable state.
    await flattenPosition(request, 'AAPL');
    await removeFromWatchlist(request, 'PYPL');
  });

  test('send a message: loading shows, mock reply and inline actions render', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await expect(page.getByTestId('connection-status')).toHaveAttribute(
      'data-state',
      'connected',
      { timeout: 20_000 }
    );

    // Mock mode answers near-instantly; delay the POST slightly so the loading
    // indicator is reliably observable (the real request still goes through).
    await page.route('**/api/chat/', async (route) => {
      if (route.request().method() === 'POST') {
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      await route.continue();
    });

    await page
      .getByPlaceholder(/Ask FinAlly/)
      .fill('Buy some AAPL and watch PYPL for me');
    await page.getByRole('button', { name: 'Send' }).click();

    // Loading indicator appears, then clears.
    await expect(page.getByTestId('chat-loading')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId('chat-loading')).toBeHidden({ timeout: 20_000 });

    // Deterministic assistant reply renders (last = newest after auto-scroll;
    // history may contain earlier copies from retried runs).
    await expect(page.getByText(MOCK_REPLY).last()).toBeVisible({ timeout: 10_000 });

    // Executed actions surface inline as confirmation badges.
    await expect(page.getByText(/Bought 5 AAPL/).last()).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/Added PYPL/).last()).toBeVisible({ timeout: 10_000 });

    // The mock trade really executed: an AAPL position now exists.
    const portfolio = await getPortfolio(request);
    const aapl = portfolio.positions.find((p) => p.ticker === 'AAPL');
    expect(aapl?.quantity).toBeGreaterThanOrEqual(5);

    // Tidy up (later specs also clean defensively).
    await flattenPosition(request, 'AAPL');
    await removeFromWatchlist(request, 'PYPL');
  });
});
