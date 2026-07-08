import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

/**
 * Strategy hub E2E (P2_STRATEGY_CONTRACT.md §8/§10, US project).
 *
 * Covers the six contract scenarios:
 *   ① template card instantiates the form → submitted strategy appears as draft
 *   ② strategy detail runs a backtest → /runs lists the persisted run →
 *     /run?id=X renders chart + stats
 *   ③ soft-gate deploy: with runs_count === 0 the first click only arms a
 *     confirmation; the second click flips the strategy to live
 *   ④ Backtest tab result saved via backtest-save → visible in the run library
 *   ⑤ chat (LLM_MOCK) message containing "strategy" → StrategyBadge renders
 *     and the created strategy is a draft in the list
 *   ⑥ nav round-trip desk → /strategies → /runs → desk keeps the SSE
 *     connection alive (client-side routing)
 *
 * Testid contract (P2_STRATEGY_CONTRACT.md §8): nav-strategies, nav-runs,
 * template-card-${key}, strategy-form, strategy-row-${id},
 * strategy-status-${id}, strategy-config, strategy-deploy, strategy-pause,
 * strategy-run-backtest, run-row-${id}, run-detail, backtest-save,
 * strategy-badge-created. backtest-chart/backtest-stats come from the
 * extracted BacktestPanel components (§8 pure refactor: testids unchanged);
 * connection-status / tab-backtest / backtest-run / trade-buy-button come
 * from the existing frontend contract.
 *
 * All API fixtures run as the anonymous 'default' user — same principal the
 * browser session uses — so API-created strategies render in the page UI.
 */

/** Wait for the live price stream (same pattern as the other specs). */
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
  template: string | null;
}

/** GET /api/strategies (default view: everything except archived). */
async function listStrategies(
  request: APIRequestContext
): Promise<StrategySummary[]> {
  const res = await request.get('/api/strategies');
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { strategies: StrategySummary[] }).strategies;
}

/** Entry condition that can never fire (price ≥ 9,999,999) — keeps a live
 *  strategy inert so the 1s engine loop never trades during the test. */
const NEVER_FIRING_ENTRY = {
  all: [{ field: 'price', op: 'above', value: 9_999_999 }],
};

/**
 * Create a draft strategy through the public API and resolve its id via the
 * list endpoint (the unique name is the lookup key — no reliance on the POST
 * response shape).
 */
async function createStrategy(
  request: APIRequestContext,
  name: string,
  overrides: Record<string, unknown> = {}
): Promise<string> {
  const res = await request.post('/api/strategies', {
    data: {
      name,
      ticker: 'NVDA',
      entry: NEVER_FIRING_ENTRY,
      // At least one exit → the strategy is deployable (contract §2).
      exits: { stop_loss_pct: 5 },
      sizing: { mode: 'fixed_qty', qty: 1 },
      ...overrides,
    },
  });
  expect(res.ok()).toBeTruthy();
  const created = (await listStrategies(request)).find((s) => s.name === name);
  expect(created).toBeTruthy();
  return created!.id;
}

/** Status of one strategy via the list endpoint (null once deleted). */
async function getStrategyStatus(
  request: APIRequestContext,
  id: string
): Promise<string | null> {
  const match = (await listStrategies(request)).find((s) => s.id === id);
  return match?.status ?? null;
}

/**
 * Cleanup: archive (legal from any state, clears open state) then delete —
 * DELETE on a live strategy is a 400 by contract. Best-effort, never asserts.
 */
async function deleteStrategy(
  request: APIRequestContext,
  id: string
): Promise<void> {
  await request.patch(`/api/strategies/${id}`, { data: { status: 'archived' } });
  await request.delete(`/api/strategies/${id}`);
}

/** Cleanup: drop every persisted run belonging to a strategy. */
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

/** Ids of every run currently in the library (for before/after diffs). */
async function runIdSet(request: APIRequestContext): Promise<Set<string>> {
  const res = await request.get('/api/backtest/runs?limit=200');
  expect(res.ok()).toBeTruthy();
  const { runs } = (await res.json()) as { runs: Array<{ id: string }> };
  return new Set(runs.map((r) => r.id));
}

test.describe('strategy hub', () => {
  test('① template card instantiates the form and submits a draft strategy', async ({
    page,
    request,
  }) => {
    await page.goto('/strategies/');
    await waitForConnected(page);

    // The six fixed templates all render as cards (contract §6 registry).
    await expect(page.locator('[data-testid^="template-card-"]')).toHaveCount(6, {
      timeout: 15_000,
    });

    // Clicking a card prefills the builder form (entry/exits/sizing).
    await page.getByTestId('template-card-dip_buyer').click();
    const form = page.getByTestId('strategy-form');
    await expect(form).toBeVisible({ timeout: 15_000 });

    // Name is the unique lookup key; ticker is the datalist-backed input
    // (contract §8: "ticker(datalist)").
    const name = `E2E dip ${Date.now()}`;
    await form.getByLabel(/name/i).first().fill(name);
    await form.locator('input[list]').first().fill('AAPL');
    await form.locator('button[type="submit"]').click();

    // POST /api/strategies → the new strategy exists as a draft.
    let id = '';
    await expect
      .poll(
        async () => {
          const match = (await listStrategies(request)).find(
            (s) => s.name === name
          );
          id = match?.id ?? '';
          return match?.status ?? null;
        },
        { timeout: 15_000 }
      )
      .toBe('draft');

    // …and the list UI shows the row with a draft status chip.
    await expect(page.getByTestId(`strategy-row-${id}`)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId(`strategy-status-${id}`)).toContainText(
      /draft/i
    );

    await deleteStrategy(request, id);
  });

  test('② strategy detail runs a backtest, the run library lists it, run detail renders', async ({
    page,
    request,
  }) => {
    const id = await createStrategy(request, `E2E bt ${Date.now()}`);

    await page.goto(`/strategy/?id=${id}`);
    await waitForConnected(page);
    // Past the strategy-empty hydration placeholder → config summary renders.
    await expect(page.getByTestId('strategy-config')).toBeVisible({
      timeout: 15_000,
    });

    // Kick off a persisted backtest (POST /api/backtest/runs {strategy_id}).
    await page.getByTestId('strategy-run-backtest').click();

    // The strategy's own runs list gains a row once the server has computed
    // and stored the run.
    await expect(page.locator('[data-testid^="run-row-"]').first()).toBeVisible({
      timeout: 30_000,
    });

    // Resolve the persisted run through the API.
    const res = await request.get(`/api/backtest/runs?strategy_id=${id}`);
    expect(res.ok()).toBeTruthy();
    const { runs } = (await res.json()) as { runs: Array<{ id: string }> };
    expect(runs.length).toBeGreaterThanOrEqual(1);
    const runId = runs[0].id;

    // The run library page lists it.
    await page.goto('/runs/');
    await waitForConnected(page);
    await expect(page.getByTestId(`run-row-${runId}`)).toBeVisible({
      timeout: 15_000,
    });

    // Run detail renders the full composition: stats (a % return figure) and
    // the equity chart (extracted EquityChart keeps its backtest-chart testid).
    await page.goto(`/run/?id=${runId}`);
    await waitForConnected(page);
    const detail = page.getByTestId('run-detail');
    await expect(detail).toBeVisible({ timeout: 15_000 });
    await expect(detail).toContainText('%', { timeout: 15_000 });
    await expect(page.getByTestId('backtest-chart')).toBeVisible({
      timeout: 15_000,
    });

    await deleteRunsForStrategy(request, id);
    await deleteStrategy(request, id);
  });

  test('③ deploying an untested strategy needs a second confirming click, then goes live', async ({
    page,
    request,
  }) => {
    // Fresh strategy: runs_count === 0 → the soft deploy gate applies.
    const id = await createStrategy(request, `E2E deploy ${Date.now()}`);

    await page.goto(`/strategy/?id=${id}`);
    await waitForConnected(page);
    await expect(page.getByTestId('strategy-config')).toBeVisible({
      timeout: 15_000,
    });

    const deploy = page.getByTestId('strategy-deploy');
    await expect(deploy).toBeVisible({ timeout: 15_000 });
    await deploy.click();

    // Soft gate: the first click only arms the confirmation — the armed
    // warning renders, the status chip still shows draft, and the strategy
    // is still a draft server-side (the backend never saw a PATCH).
    await expect(page.getByTestId('strategy-deploy-warning')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId('strategy-status')).toContainText(/draft/i);
    expect(await getStrategyStatus(request, id)).toBe('draft');

    // Second click confirms → live.
    await deploy.click();
    await expect
      .poll(() => getStrategyStatus(request, id), { timeout: 15_000 })
      .toBe('live');

    // Live controls are available (pause per contract §8 header controls).
    await expect(page.getByTestId('strategy-pause')).toBeVisible({
      timeout: 15_000,
    });

    // Cleanup (archive → delete; the never-firing entry kept the engine inert).
    await deleteStrategy(request, id);
  });

  test('④ saving a Backtest-tab result persists it to the run library', async ({
    page,
    request,
  }) => {
    const before = await runIdSet(request);

    await page.goto('/');
    await waitForConnected(page);
    await page.getByTestId('tab-backtest').click();
    await expect(page.getByTestId('backtest-run')).toBeVisible();

    // Small horizon keeps the synthetic-history compute snappy in CI
    // (same pattern as backtest.spec.ts).
    await page.getByLabel('Days', { exact: true }).fill('10');
    await page.getByTestId('backtest-run').click();
    await expect(page.getByTestId('backtest-stats')).toBeVisible({
      timeout: 20_000,
    });

    // Save only exists once a result is rendered; the server re-runs the same
    // config+seed and persists (contract §5).
    const save = page.getByTestId('backtest-save');
    await expect(save).toBeVisible({ timeout: 15_000 });
    await save.click();

    // A new run id shows up in the library.
    let newId = '';
    await expect
      .poll(
        async () => {
          const fresh = [...(await runIdSet(request))].filter(
            (x) => !before.has(x)
          );
          newId = fresh[0] ?? '';
          return fresh.length;
        },
        { timeout: 20_000 }
      )
      .toBeGreaterThanOrEqual(1);

    // …and the /runs page renders its row.
    await page.goto('/runs/');
    await waitForConnected(page);
    await expect(page.getByTestId(`run-row-${newId}`)).toBeVisible({
      timeout: 15_000,
    });

    await request.delete(`/api/backtest/runs/${newId}`);
  });

  test('⑤ chat (LLM_MOCK) "strategy" message creates a draft with a StrategyBadge inline', async ({
    page,
    request,
  }) => {
    const before = new Set((await listStrategies(request)).map((s) => s.id));

    await page.goto('/');
    await waitForConnected(page);

    // Contains "strategy" (and not "backtest") → deterministic LLM_MOCK
    // branch: create ma_golden_cross on NVDA + a persisted 20-day backtest
    // (contract §7).
    const input = page.getByPlaceholder(/Ask FinAlly/);
    await input.fill('Set up a strategy for NVDA and test it');
    await input.press('Enter');

    // The created-strategy badge renders inline in the conversation
    // (last = newest; retried runs may have left earlier copies).
    await expect(page.getByTestId('strategy-badge-created').last()).toBeVisible({
      timeout: 30_000,
    });

    // The strategy really exists — as a draft (chat create never deploys).
    let id = '';
    await expect
      .poll(
        async () => {
          const created = (await listStrategies(request)).find(
            (s) => !before.has(s.id) && s.ticker === 'NVDA'
          );
          id = created?.id ?? '';
          return created?.status ?? null;
        },
        { timeout: 15_000 }
      )
      .toBe('draft');

    // …and the strategies page lists it as a draft row.
    await page.goto('/strategies/');
    await waitForConnected(page);
    await expect(page.getByTestId(`strategy-row-${id}`)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId(`strategy-status-${id}`)).toContainText(
      /draft/i
    );

    // Tidy up: the mock branch also persisted a run for this strategy.
    await deleteRunsForStrategy(request, id);
    await deleteStrategy(request, id);
  });

  test('⑥ nav round-trip desk → strategies → runs → desk keeps the SSE connection alive', async ({
    page,
  }) => {
    await page.goto('/');
    await waitForConnected(page);
    const indicator = page.getByTestId('connection-status');

    // Marker survives client-side routing only — a full reload (which would
    // tear down the app-level EventSource) clears it (pages.spec.ts pattern).
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__e2eStrategyNavMarker =
        'alive';
    });

    // → /strategies
    await page.getByTestId('nav-strategies').click();
    await expect(page).toHaveURL(/\/strategies\/?(\?.*)?$/);
    await expect(
      page.locator('[data-testid^="template-card-"]').first()
    ).toBeVisible({ timeout: 15_000 });
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // → /runs
    await page.getByTestId('nav-runs').click();
    await expect(page).toHaveURL(/\/runs\/?(\?.*)?$/);
    await expect(indicator).toHaveAttribute('data-state', 'connected', {
      timeout: 5_000,
    });

    // → back to the trading desk (anchored origin-root match, pages.spec.ts).
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
      () => (window as unknown as Record<string, unknown>).__e2eStrategyNavMarker
    );
    expect(marker).toBe('alive');
  });
});
