import { test, expect } from '@playwright/test';

/**
 * A-share (CN) market E2E (CN-4a).
 *
 * Runs ONLY against a FINALLY_MARKET=cn backend via docker-compose.cn.test.yml
 * (which sets CN_E2E=1 so playwright.config.ts swaps to a dependency-free `cn`
 * project). The default US run skips this file (playwright.config testIgnore).
 *
 * Deterministic checks (LLM_MOCK=true, no chat needed):
 *   (a) /api/market/profile → market cn / lot_size 100 / up_is_red true
 *   (b) <html data-market="cn">
 *   (c) CN ticker 600519 + Chinese name in the watchlist, Chinese UI chrome
 *   (d) connection dot pinned green (rgb(34,197,94)) — status semantics never flip
 *   (e) red-up: --color-up resolves to #ef4444 under data-market=cn
 *   (f) integer-lot rejection: a 150-share buy of 600519 → 400 with the 整手 error
 *
 * Uses the connection-status testid + waitForStream pattern from the other specs.
 */

async function waitForStream(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('A-share (CN) market', () => {
  test('(a) the market-profile endpoint reports the CN profile', async ({ request }) => {
    const res = await request.get('/api/market/profile');
    expect(res.ok()).toBeTruthy();
    const profile = await res.json();
    expect(profile.market).toBe('cn');
    expect(profile.lot_size).toBe(100);
    expect(profile.up_is_red).toBe(true);
  });

  test('(b–e) the UI is Chinese, red-up, with a pinned green status dot', async ({ page }) => {
    await waitForStream(page);

    // (b) <html data-market="cn"> — stamped once the profile SWR resolves.
    await expect
      .poll(() => page.evaluate(() => document.documentElement.getAttribute('data-market')), {
        timeout: 15_000,
      })
      .toBe('cn');

    // (c) The CN ticker 600519 and its Chinese name appear in the watchlist,
    // and the surrounding chrome is Chinese (zh dict: 持仓 / 买入).
    await expect(page.getByText('600519').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('贵州茅台').first()).toBeVisible();
    await expect(page.getByTestId('tab-positions')).toContainText('持仓');
    await expect(page.getByTestId('trade-buy-button')).toContainText('买入');

    // (e) Red-up: the direction CSS variable resolves to the red hex under
    // data-market=cn (proves the globals.css :root[data-market='cn'] override).
    const colorUp = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-up').trim()
    );
    expect(colorUp).toBe('#ef4444');

    // (d) The connection dot encodes STATUS (green = connected), not price
    // direction, so it must stay green even on the red-up A-share market. The
    // attribute-selector pin resolves the computed background to the green.
    const dotBg = await page
      .getByTestId('connection-status')
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(dotBg).toBe('rgb(34, 197, 94)');
  });

  test('(f) rejects a non-round-lot buy with the Chinese 整手 error', async ({ request }) => {
    // 150 shares is not a multiple of the 100-share board lot → hard reject.
    const res = await request.post('/api/portfolio/trade', {
      data: { ticker: '600519', quantity: 150, side: 'buy' },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    // Backend message: "A股买入须为 100 股的整数倍"
    expect(body.error).toContain('整数倍');
  });
});
