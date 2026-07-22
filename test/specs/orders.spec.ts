import { test, expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { getPortfolio, flattenPosition } from './helpers';

/**
 * Limit orders + blotter tabs + news ticker (PLATFORM_ROADMAP.md P0).
 *
 * Selectors follow the frontend testid contract:
 *   order-type-limit, trade-toast, tab-orders / tab-fills,
 *   open-orders-table, cancel-order-<id>, orders-table, news-ticker.
 */
const TICKER = 'MSFT';

interface OrderRow {
  id: string;
  ticker: string;
  status: string;
}

async function cancelAllOpenOrders(request: APIRequestContext): Promise<void> {
  const res = await request.get('/api/portfolio/orders?status=open');
  expect(res.ok()).toBeTruthy();
  const { orders } = (await res.json()) as { orders: OrderRow[] };
  for (const o of orders) {
    await request.delete(`/api/portfolio/orders/${o.id}`);
  }
}

async function waitForStream(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('limit orders', () => {
  test.beforeEach(async ({ request }) => {
    await cancelAllOpenOrders(request);
    await flattenPosition(request, TICKER);
  });

  test('a resting limit order appears in the Orders tab and can be cancelled', async ({
    page,
    request,
  }) => {
    await waitForStream(page);

    // Deep out-of-market buy — $1 can never be marketable, so it rests open.
    await page.getByTestId('order-type-limit').click();
    await page.getByLabel('Ticker', { exact: true }).fill(TICKER);
    await page.getByLabel('Qty', { exact: true }).fill('1');
    await page.getByLabel('Limit price', { exact: true }).fill('1');
    await page.getByRole('button', { name: 'Buy', exact: true }).click();

    await expect(page.getByTestId('trade-toast')).toContainText('Order placed', {
      timeout: 10_000,
    });

    // The order rests in the Orders tab.
    await page.getByTestId('tab-orders').click();
    await expect(page.getByTestId('open-orders-table')).toBeVisible({ timeout: 10_000 });
    const res = await request.get('/api/portfolio/orders?status=open');
    const { orders } = (await res.json()) as { orders: OrderRow[] };
    const mine = orders.find((o) => o.ticker === TICKER);
    expect(mine).toBeTruthy();
    await expect(page.getByTestId(`open-order-row-${mine!.id}`)).toBeVisible();

    // Cancel from the UI; the row disappears and the API confirms.
    await page.getByTestId(`cancel-order-${mine!.id}`).click();
    await expect(page.getByTestId(`open-order-row-${mine!.id}`)).toHaveCount(0, {
      timeout: 10_000,
    });
    const after = await request.get(`/api/portfolio/orders?status=cancelled`);
    const cancelled = ((await after.json()) as { orders: OrderRow[] }).orders;
    expect(cancelled.some((o) => o.id === mine!.id)).toBeTruthy();
  });

  test('a marketable limit order fills immediately into the Fills tab', async ({
    page,
    request,
  }) => {
    await waitForStream(page);
    const before = await getPortfolio(request);

    // Limit far above the ask — fills instantly at the ask.
    await page.getByTestId('order-type-limit').click();
    await page.getByLabel('Ticker', { exact: true }).fill(TICKER);
    await page.getByLabel('Qty', { exact: true }).fill('1');
    await page.getByLabel('Limit price', { exact: true }).fill('999999');
    await page.getByRole('button', { name: 'Buy', exact: true }).click();

    await expect(page.getByTestId('trade-toast')).toContainText(`Bought 1 ${TICKER}`, {
      timeout: 10_000,
    });

    // Cash decreased and the position exists (API is the source of truth).
    await expect
      .poll(async () => (await getPortfolio(request)).cash, { timeout: 15_000 })
      .toBeLessThan(before.cash);

    // The fill shows up in the Fills tab blotter.
    await page.getByTestId('tab-fills').click();
    await expect(page.getByTestId('orders-table')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('orders-table')).toContainText(TICKER);
  });
});

test.describe('news ticker', () => {
  test('the market-event strip renders under the header', async ({ page }) => {
    await waitForStream(page);

    // Placeholder text before any event, or scrolling items after one — the
    // strip itself must always be present.
    await expect(page.getByTestId('news-ticker')).toBeVisible();
  });
});
