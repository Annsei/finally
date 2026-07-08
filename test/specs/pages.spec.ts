import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { getPortfolio, flattenPosition } from './helpers';

/**
 * Multi-page workstation E2E (P1_PAGES_CONTRACT.md §9, US project).
 *
 * Covers the seven contract scenarios:
 *   ① nav round-trip across all four pages, connection dot stays "connected"
 *     (client-side routing — the single app-level SSE stream must not drop)
 *   ② deep links: direct /market/ AND slashless /market (307 → /market/)
 *     both render the market grid
 *   ③ market grid has rows and prices tick
 *   ④ clicking a grid row lands on /symbol?c=… with chart + stats rendered
 *   ⑤ trading from the symbol page works (reuses the trade-flow assertions)
 *   ⑥ journal "run review" (LLM_MOCK) produces an archived review entry
 *   ⑦ arena renders the leaderboard and the season history
 *
 * Testid contract: nav-desk|nav-market|nav-journal|nav-arena, market-grid,
 * market-row-${ticker}, market-events, symbol-stats, symbol-position,
 * journal-run-review, journal-reviews, arena-seasons, arena-season-${id}
 * (P1_PAGES_CONTRACT.md §2, §4–§7). connection-status / trade-* / tf-* /
 * leaderboard come from the existing frontend contract.
 */

/** Wait for the live price stream (same pattern as the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('multi-page workstation', () => {
  test('① nav round-trip keeps the SSE connection alive on every page', async ({
    page,
  }) => {
    await page.goto('/');
    await waitForConnected(page);
    const indicator = page.getByTestId('connection-status');

    // Marker survives client-side routing only — a full reload (which would
    // tear down the app-level EventSource) clears it.
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__e2eNavMarker = 'alive';
    });

    // → /market
    await page.getByTestId('nav-market').click();
    await expect(page).toHaveURL(/\/market\/?(\?.*)?$/);
    await expect(page.getByTestId('market-grid')).toBeVisible({ timeout: 15_000 });
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // → /journal
    await page.getByTestId('nav-journal').click();
    await expect(page).toHaveURL(/\/journal\/?(\?.*)?$/);
    await expect(page.getByTestId('journal-run-review')).toBeVisible({
      timeout: 15_000,
    });
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // → /arena
    await page.getByTestId('nav-arena').click();
    await expect(page).toHaveURL(/\/arena\/?(\?.*)?$/);
    await expect(page.getByTestId('arena-seasons')).toBeVisible({ timeout: 15_000 });
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // → back to the trading desk — anchor the origin root explicitly: a
    // suffix-only /\/(\?.*)?$/ is vacuously true for any trailing-slash URL
    // (e.g. /market/), so it must match "scheme://host/" exactly.
    await page.getByTestId('nav-desk').click();
    await expect(page).toHaveURL(new RegExp('^https?://[^/]+/(\\?.*)?$'));
    await expect(page.getByTestId('trade-buy-button')).toBeVisible({
      timeout: 15_000,
    });
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // No full page load happened anywhere in the round-trip.
    const marker = await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__e2eNavMarker
    );
    expect(marker).toBe('alive');
  });

  test('② deep links: /market/ direct and slashless /market (307) both render', async ({
    page,
    request,
  }) => {
    // Slashless path redirects (Starlette StaticFiles html=True → 307 …/market/).
    const redirect = await request.get('/market', { maxRedirects: 0 });
    expect(redirect.status()).toBe(307);
    expect(redirect.headers()['location']).toContain('/market/');

    // Direct trailing-slash deep link renders the grid.
    await page.goto('/market/');
    await waitForConnected(page);
    await expect(page.getByTestId('market-grid')).toBeVisible({ timeout: 15_000 });

    // Slashless deep link follows the redirect and still renders.
    await page.goto('/market');
    await expect(page).toHaveURL(/\/market\/(\?.*)?$/);
    await waitForConnected(page);
    await expect(page.getByTestId('market-grid')).toBeVisible({ timeout: 15_000 });
  });

  test('③ market grid has rows and prices tick', async ({ page }) => {
    await page.goto('/market/');
    await waitForConnected(page);

    await expect(page.getByTestId('market-grid')).toBeVisible({ timeout: 15_000 });

    // AAPL is in the US universe — its row renders with a price.
    const row = page.getByTestId('market-row-AAPL');
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/\d+\.\d{2}/, { timeout: 15_000 });

    // Live: the row content changes within ~10s (simulator ticks every ~500ms;
    // price and volume update on every tick).
    const initial = ((await row.textContent()) ?? '').trim();
    await expect(row).not.toHaveText(initial, { timeout: 10_000 });

    // The event archive section is mounted (empty state is fine on a fresh boot).
    await expect(page.getByTestId('market-events')).toBeVisible({ timeout: 15_000 });
  });

  test('④ clicking the grid code link opens /symbol?c=… with chart and stats', async ({
    page,
  }) => {
    await page.goto('/market/');
    await waitForConnected(page);

    const row = page.getByTestId('market-row-NVDA');
    await expect(row).toBeVisible({ timeout: 15_000 });

    // Contract §4: codes route through SymbolLink — click the link itself,
    // not the row (the row onClick is a convenience shortcut; both resolve to
    // the same destination, so bubbling is harmless). Scope by row so the
    // event archive's symbol-link-NVDA can never collide.
    const link = row.getByTestId('symbol-link-NVDA');
    await expect(link).toHaveAttribute('href', /\/symbol\/?\?(.*&)?c=NVDA/);
    await link.click();

    await expect(page).toHaveURL(/\/symbol\/?\?(.*&)?c=NVDA/, { timeout: 15_000 });

    // Day stats panel renders (past the symbol-empty hydration placeholder)…
    await expect(page.getByTestId('symbol-stats')).toBeVisible({ timeout: 15_000 });
    // …and the reused MainChart is mounted (its timeframe switcher is static UI).
    await expect(page.getByTestId('tf-1s')).toBeVisible({ timeout: 15_000 });
  });

  test('⑤ trading from the symbol page: buy fills, cash drops, position appears', async ({
    page,
    request,
  }) => {
    const TICKER = 'NVDA';
    const QTY = 2;
    // Clean slate (retried runs / earlier specs may have left a position).
    await flattenPosition(request, TICKER);

    await page.goto(`/symbol/?c=${TICKER}`);
    await waitForConnected(page);
    await expect(page.getByTestId('symbol-stats')).toBeVisible({ timeout: 15_000 });

    const before = await getPortfolio(request);

    // Same trade-flow pattern as trade.spec.ts — the reused TradeBar fills at
    // the live price. Fill the ticker explicitly (autofill from ?c= is a
    // convenience, not the assertion target).
    await page.getByLabel('Ticker', { exact: true }).fill(TICKER);
    await page.getByLabel('Qty', { exact: true }).fill(String(QTY));
    await page.getByTestId('trade-buy-button').click();

    // Fill toast confirms the market order executed from this page.
    await expect(page.getByTestId('trade-toast')).toBeVisible({ timeout: 15_000 });

    // Cash decreased and the position exists with the bought quantity.
    await expect
      .poll(async () => (await getPortfolio(request)).cash, { timeout: 15_000 })
      .toBeLessThan(before.cash);
    const after = await getPortfolio(request);
    const position = after.positions.find((p) => p.ticker === TICKER);
    expect(position?.quantity).toBeCloseTo(QTY, 5);

    // The "my position" panel on the symbol page reflects the holding.
    await expect(page.getByTestId('symbol-position')).toBeVisible({
      timeout: 15_000,
    });

    // Tidy up so later specs (and retries) start flat.
    await flattenPosition(request, TICKER);
  });

  test('⑥ journal: run review (LLM_MOCK) adds an archived review entry', async ({
    page,
  }) => {
    await page.goto('/journal/');
    await waitForConnected(page);

    const runButton = page.getByTestId('journal-run-review');
    await expect(runButton).toBeVisible({ timeout: 15_000 });
    await runButton.click();

    // LLM_MOCK=true → deterministic review text (backend/app/routes/chat.py):
    // "[MOCK REVIEW] You made N trades today. …" appears in the archive list.
    await expect(page.getByTestId('journal-reviews')).toContainText(
      '[MOCK REVIEW]',
      { timeout: 20_000 }
    );
  });

  test('⑦ arena renders the leaderboard and season history', async ({ page }) => {
    await page.goto('/arena/');
    await waitForConnected(page);

    // Zero-modification <Leaderboard/> mount (existing testid contract).
    await expect(page.getByTestId('leaderboard')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('leaderboard-table')).toBeVisible({
      timeout: 15_000,
    });

    // Season history: init_db guarantees season 1 exists, so at least one
    // arena-season-${id} item renders inside arena-seasons.
    await expect(page.getByTestId('arena-seasons')).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator('[data-testid^="arena-season-"]').first()
    ).toBeVisible({ timeout: 15_000 });
  });
});
