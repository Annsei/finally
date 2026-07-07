import { test, expect } from '@playwright/test';

/**
 * Strategy backtester (PLATFORM_ROADMAP.md M5).
 *
 * Selectors follow the frontend testid contract:
 *   tab-backtest, backtest-run, backtest-stats, backtest-return,
 *   backtest-chart, backtest-badge-completed.
 *
 * POST /api/backtest is stateless compute — no cleanup needed. The chat
 * test relies on the LLM_MOCK "backtest" branch (deterministic NVDA
 * dip-buy instruction).
 */

async function waitForStream(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

test.describe('strategy backtester', () => {
  test('running a backtest from the Backtest tab renders stats, chart and trades', async ({
    page,
  }) => {
    await waitForStream(page);

    await page.getByTestId('tab-backtest').click();
    await expect(page.getByTestId('backtest-run')).toBeVisible();

    // Small horizon keeps the synthetic-history compute snappy in CI.
    await page.getByLabel('Days', { exact: true }).fill('10');
    await page.getByTestId('backtest-run').click();

    await expect(page.getByTestId('backtest-stats')).toBeVisible({ timeout: 20_000 });
    // Return card carries a signed percentage from the engine.
    await expect(page.getByTestId('backtest-return')).toContainText('%');
    await expect(page.getByTestId('backtest-chart')).toBeVisible();
  });

  test('the AI runs a backtest from chat (mocked) and a stats badge appears', async ({
    page,
  }) => {
    await waitForStream(page);

    const input = page.getByPlaceholder('Ask FinAlly about your portfolio…');
    await input.fill('run a backtest of buying NVDA dips');
    await input.press('Enter');

    // LLM_MOCK "backtest" branch → deterministic NVDA dip-buy instruction,
    // executed server-side; the badge renders compact stats.
    await expect(page.getByTestId('backtest-badge-completed').last()).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId('backtest-badge-completed').last()).toContainText(
      'Backtest NVDA'
    );
  });
});
