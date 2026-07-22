import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

/**
 * A-share (CN) strategy hub E2E (P2_STRATEGY_CONTRACT.md §9/§10).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as cn.spec.ts /
 * pages-cn.spec.ts. Launched via docker-compose.cn.test.yml
 * (FINALLY_MARKET=cn, LLM_MOCK=true).
 *
 * CN acceptance for the strategy hub (contract §9/§10):
 *   (a) strategies navigation and the six template cards render in Chinese
 *   (b) a created 600519 strategy appears in the list with a Chinese status
 *       chip and ¥-formatted P&L (formatMoney)
 *   (c) the detail page renders the Chinese condition summary with ¥ amounts
 *   (d) a zh chat message containing 「策略」 (LLM_MOCK branch) creates a
 *       strategy and renders the created + backtest badges inline
 *
 * API fixtures run as the anonymous 'default' user — the same principal the
 * browser session uses — so API-created strategies render in the page UI.
 */

const CJK = /[一-鿿]/;

/** Wait for the live stream (connection-status pattern from the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

interface StrategySummary {
  id: string;
  name: string;
  ticker: string;
  status: string;
}

/** GET /api/strategies (default view: everything except archived). */
async function listStrategies(
  request: APIRequestContext
): Promise<StrategySummary[]> {
  const res = await request.get('/api/strategies');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { strategies: StrategySummary[] }).strategies;
}

/**
 * Create a draft 600519 strategy via the API (CN universe ticker, one lot).
 * The unique name is the id lookup key — no reliance on the POST body shape.
 */
async function createCnStrategy(
  request: APIRequestContext,
  name: string,
  entry: Record<string, unknown>
): Promise<string> {
  const res = await request.post('/api/strategies', {
    data: {
      name,
      ticker: '600519',
      entry,
      exits: { take_profit_pct: 4, stop_loss_pct: 3 },
      // One A-share lot — valid under the CN whole-lot mechanics.
      sizing: { mode: 'fixed_qty', qty: 100 },
    },
  });
  expect(res.ok()).toBeTruthy();
  const created = (await listStrategies(request)).find((s) => s.name === name);
  expect(created).toBeTruthy();
  return created!.id;
}

/** Cleanup: archive (legal from any state) then delete. Best-effort. */
async function deleteStrategy(
  request: APIRequestContext,
  id: string
): Promise<void> {
  await request.patch(`/api/strategies/${id}`, { data: { status: 'archived' } });
  await request.delete(`/api/strategies/${id}`);
}

/** Cleanup: drop every persisted run belonging to a strategy. Best-effort. */
async function deleteRunsForStrategy(
  request: APIRequestContext,
  id: string
): Promise<void> {
  const res = await request.get(`/api/backtest/runs?strategy_id=${id}`);
  if (!res.ok()) return;
  const { runs } = (await res.json()) as { runs: Array<{ id: string }> };
  for (const run of runs) {
    await request.delete(`/api/backtest/runs/${run.id}`);
  }
}

test.describe('A-share (CN) strategy hub', () => {
  test('(a) strategies navigation and the six template cards are Chinese', async ({
    page,
  }) => {
    await page.goto('/');
    await waitForConnected(page);

    // Both new nav entries render with Chinese labels (zh dict nav.*).
    for (const id of ['nav-strategies', 'nav-runs']) {
      const link = page.getByTestId(id);
      await expect(link).toBeVisible({ timeout: 15_000 });
      await expect(link).toHaveText(CJK);
    }

    // Client-side route to the strategy hub.
    await page.getByTestId('nav-strategies').click();
    await expect(page).toHaveURL(/\/strategies\/?(\?.*)?$/);

    // All six fixed templates render (contract §6 registry), and the card
    // copy is Chinese (strategy.template.{key}.name/.desc from the zh dict).
    await expect(page.locator('[data-testid^="template-card-"]')).toHaveCount(6, {
      timeout: 15_000,
    });
    await expect(page.getByTestId('template-card-dip_buyer')).toContainText(CJK);
  });

  test('(b) a created 600519 strategy lists with a Chinese status chip and ¥ P&L', async ({
    page,
    request,
  }) => {
    const name = `茅台回调 ${Date.now()}`;
    const id = await createCnStrategy(request, name, {
      all: [{ field: 'day_change_pct', op: 'below', value: -3 }],
    });

    await page.goto('/strategies/');
    await waitForConnected(page);

    // The list row renders: name + code + Chinese status chip + ¥-formatted
    // realized P&L (formatMoney, contract §9).
    const row = page.getByTestId(`strategy-row-${id}`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText('600519');
    await expect(row).toContainText('¥');
    await expect(page.getByTestId(`strategy-status-${id}`)).toHaveText(CJK);

    await deleteStrategy(request, id);
  });

  test('(c) strategy detail renders the Chinese condition summary with ¥ amounts', async ({
    page,
    request,
  }) => {
    // A price-level entry condition: its value is money, so the conditionText
    // rendering must show ¥ (formatMoney) alongside Chinese copy.
    const id = await createCnStrategy(request, `价格条件 ${Date.now()}`, {
      all: [{ field: 'price', op: 'below', value: 1200 }],
    });

    await page.goto(`/strategy/?id=${id}`);
    await waitForConnected(page);

    // Past the strategy-empty hydration placeholder → the human-readable
    // entry/exits/sizing summary renders in Chinese with ¥ amounts.
    const config = page.getByTestId('strategy-config');
    await expect(config).toBeVisible({ timeout: 15_000 });
    await expect(config).toContainText(CJK);
    await expect(config).toContainText('¥');

    await deleteStrategy(request, id);
  });

  test('(d) a zh chat message containing 「策略」 creates a strategy with inline badges', async ({
    page,
    request,
  }) => {
    const before = new Set((await listStrategies(request)).map((s) => s.id));

    await page.goto('/');
    await waitForConnected(page);

    // Contains 「策略」 (and no backtest keyword) → the deterministic LLM_MOCK
    // strategy branch: create ma_golden_cross on the first CN universe ticker
    // (600519) + a persisted 20-day backtest (contract §7/§9). The zh chat
    // placeholder still contains the product name, so /FinAlly/ matches both
    // locales (same lookup as the US spec).
    const input = page.getByPlaceholder(/FinAlly/);
    await input.fill('帮我给茅台建一个均线策略');
    await input.press('Enter');

    // Both action badges render inline in the conversation (last = newest;
    // retried runs may have left earlier copies) — the create confirmation
    // and the compact backtest stats line.
    await expect(page.getByTestId('strategy-badge-created').last()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByTestId('strategy-badge-backtest').last()).toBeVisible({
      timeout: 30_000,
    });

    // Tidy up: resolve the created 600519 strategy, then drop its persisted
    // run and the strategy itself (same best-effort cleanup as the US spec).
    let id = '';
    await expect
      .poll(
        async () => {
          const created = (await listStrategies(request)).find(
            (s) => !before.has(s.id) && s.ticker === '600519'
          );
          id = created?.id ?? '';
          return created?.status ?? null;
        },
        { timeout: 15_000 }
      )
      .toBe('draft');
    await deleteRunsForStrategy(request, id);
    await deleteStrategy(request, id);
  });
});
