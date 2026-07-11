import { test, expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import {
  ensureSampleHistory,
  getCoverage,
  switchToHistorySource,
  syncSampleHistory,
  waitForConnected,
} from './history-helpers';

/**
 * Real-history data layer + history backtest mode E2E
 * (D1_HISTORY_CONTRACT.md §6, US project).
 *
 * Covers the three contract scenarios:
 *   ① sync(sample) via the cookie-path request context → the pinned
 *     {results, total_bars} response; GET /api/market/history/coverage
 *     reports the synced range; the /market history-coverage card renders
 *     the per-ticker interval and the Guest-usable history-sync-button
 *   ② Backtest tab: the backtest-source segmented switch flips to History,
 *     the submitted POST /api/backtest carries source:"history", the run
 *     completes and the stats area renders the backtest-source-badge
 *   ③ strategy detail: strategy-bt-source flips to History,
 *     strategy-run-backtest persists a history run whose source is
 *     non-synthetic, and the /runs library lists it with a source badge
 *
 * ZERO external network (contract core invariant): every sync posts the
 * explicit `sample` source through the request context. The UI sync button
 * is source=auto — it would try a real provider first — so ① asserts its
 * presence/enabled state but never clicks it.
 *
 * Testid contract (§5): backtest-source, backtest-source-badge,
 * strategy-bt-source, history-coverage, history-sync-button.
 * connection-status / tab-backtest / backtest-run / backtest-stats /
 * backtest-return / strategy-config / strategy-run-backtest / run-row-* come
 * from the existing frontend contract.
 */

/** Both tickers the suite touches: form default universe + strategy. */
const TICKERS = ['AAPL', 'NVDA'];

/** Entry condition that can never fire (strategies.spec.ts pattern) —
 *  keeps the fixture strategy inert outside the explicit backtest run. */
const NEVER_FIRING_ENTRY = {
  all: [{ field: 'price', op: 'above', value: 9_999_999 }],
};

interface StrategySummary {
  id: string;
  name: string;
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

/** Create a draft NVDA strategy; the unique name resolves the id. */
async function createStrategy(
  request: APIRequestContext,
  name: string
): Promise<string> {
  const res = await request.post('/api/strategies', {
    data: {
      name,
      ticker: 'NVDA',
      entry: NEVER_FIRING_ENTRY,
      exits: { stop_loss_pct: 5 },
      sizing: { mode: 'fixed_qty', qty: 1 },
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

test.describe('history data layer + history backtests', () => {
  test('① sample sync fills coverage and the /market data-status card shows the range', async ({
    page,
    request,
  }) => {
    // Cookie-path sync, explicit sample source (429-tolerant polling lives
    // in the helper). Pinned response shape (contract §2): per-ticker
    // results — every row from the sample provider, error-free, with a
    // positive bar count — plus the grand total.
    const sync = await syncSampleHistory(request, TICKERS);
    expect(sync.total_bars).toBeGreaterThan(0);
    expect(sync.results).toHaveLength(TICKERS.length);
    for (const ticker of TICKERS) {
      const row = sync.results.find((r) => r.ticker === ticker);
      expect(row, `sync result for ${ticker}`).toBeTruthy();
      expect(row!.source).toBe('sample');
      expect(row!.error ?? null).toBeNull();
      expect(row!.bars).toBeGreaterThan(0);
    }

    // The coverage read-back reports a usable range per ticker (§2): a
    // dated from/to interval, ordered, with the persisted bar count.
    const coverage = await getCoverage(request);
    for (const ticker of TICKERS) {
      const row = coverage.find((c) => c.ticker === ticker);
      expect(row, `coverage row for ${ticker}`).toBeTruthy();
      expect(row!.count).toBeGreaterThanOrEqual(20);
      expect(row!.source).toBe('sample');
      expect(row!.from).toMatch(/^\d{4}-\d{2}-\d{2}/);
      expect(row!.to).toMatch(/^\d{4}-\d{2}-\d{2}/);
      expect(row!.from <= row!.to).toBe(true);
    }

    // The /market data-status card renders the synced tickers with their
    // interval (a year is the format-agnostic marker of a rendered range).
    await page.goto('/market/');
    await waitForConnected(page);
    const card = page.getByTestId('history-coverage');
    await expect(card).toBeVisible({ timeout: 15_000 });
    for (const ticker of TICKERS) {
      await expect(card).toContainText(ticker, { timeout: 15_000 });
    }
    await expect(card).toContainText(/\d{4}/);

    // Guest-usable sync affordance (§5). Never clicked: the button posts
    // source=auto, which would attempt a real provider before falling back.
    const button = page.getByTestId('history-sync-button');
    await expect(button).toBeVisible({ timeout: 15_000 });
    await expect(button).toBeEnabled();
  });

  test('② Backtest tab on the history source completes and shows the source badge', async ({
    page,
    request,
  }) => {
    // Self-sufficient: coverage may already exist from ① — the helper only
    // syncs (sample source) when bars are missing.
    await ensureSampleHistory(request, TICKERS);

    await page.goto('/');
    await waitForConnected(page);
    await page.getByTestId('tab-backtest').click();
    await expect(page.getByTestId('backtest-run')).toBeVisible();

    // Deterministic ticker with guaranteed sample coverage; the form's
    // default 30 days sits inside the history clamp (20..750 trading days).
    await page.locator('#bt-ticker').fill('AAPL');

    // Flip the data-source segmented switch (§5) to History.
    await switchToHistorySource(page, 'backtest-source');

    // The submit must carry source:"history" (§5 提交带 source) — capture
    // the browser's own POST /api/backtest before clicking.
    const requestPromise = page.waitForRequest(
      (r) =>
        r.method() === 'POST' &&
        /\/api\/backtest\/?$/.test(new URL(r.url()).pathname),
      { timeout: 20_000 }
    );
    await page.getByTestId('backtest-run').click();
    const btRequest = await requestPromise;
    const payload = btRequest.postDataJSON() as { source?: string };
    expect(payload.source).toBe('history');

    // The run completes on daily bars: stats render with a % return, and
    // the stats area carries the source badge (§5 backtest-source-badge) —
    // sample-backed history, never the synthetic label.
    await expect(page.getByTestId('backtest-stats')).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId('backtest-return')).toContainText('%');
    const badge = page.getByTestId('backtest-source-badge');
    await expect(badge).toBeVisible({ timeout: 15_000 });
    await expect(badge).toContainText(/sample|hist/i);
    await expect(badge).not.toContainText(/synthetic/i);
  });

  test('③ strategy detail runs a history backtest that lands in the run library', async ({
    page,
    request,
  }) => {
    await ensureSampleHistory(request, TICKERS);
    const id = await createStrategy(request, `E2E history bt ${Date.now()}`);

    await page.goto(`/strategy/?id=${id}`);
    await waitForConnected(page);
    // Past the strategy-empty hydration placeholder → detail rendered.
    await expect(page.getByTestId('strategy-config')).toBeVisible({
      timeout: 15_000,
    });

    // Same-style source switch on the detail page (§5 strategy-bt-source),
    // then kick off the persisted run (POST /api/backtest/runs 透传 source).
    await switchToHistorySource(page, 'strategy-bt-source');
    await page.getByTestId('strategy-run-backtest').click();

    // The strategy's own runs list gains a row once the server has
    // computed and stored the run.
    await expect(page.locator('[data-testid^="run-row-"]').first()).toBeVisible({
      timeout: 30_000,
    });

    // Resolve the persisted run and pin its provenance: a history-mode run
    // backed by sample bars is never labelled synthetic (§5 badge enum).
    const res = await request.get(`/api/backtest/runs?strategy_id=${id}`);
    expect(res.ok()).toBeTruthy();
    const { runs } = (await res.json()) as {
      runs: Array<{ id: string; source?: string }>;
    };
    expect(runs.length).toBeGreaterThanOrEqual(1);
    const run = runs[0];
    expect(String(run.source ?? '')).toMatch(/^(sample|history)$/);

    // The run library page lists it with its source badge on the row.
    await page.goto('/runs/');
    await waitForConnected(page);
    const row = page.getByTestId(`run-row-${run.id}`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/sample|hist/i);

    await deleteRunsForStrategy(request, id);
    await deleteStrategy(request, id);
  });
});
