import { expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

/**
 * Shared fixtures for the D1 history-data specs (history.spec.ts /
 * history-cn.spec.ts — D1_HISTORY_CONTRACT.md §2/§6).
 *
 * Every sync in E2E uses the explicit `sample` source: the contract's core
 * invariant is ZERO external network in tests, and only the sample provider
 * is deterministic and offline. The UI sync button is hardwired to
 * source=auto (which would attempt a real provider first), so specs assert
 * its presence but never click it.
 *
 * Not a spec file: the name ends in `-helpers.ts`, so neither the US
 * project's /\.spec\.ts/ testMatch nor the CN /cn\.spec\.ts/ testMatch
 * discovers it.
 */

/** POST /api/market/history/sync per-ticker result (contract §2). */
export interface SyncResult {
  ticker: string;
  source: string;
  bars: number;
  error?: string | null;
}

/** POST /api/market/history/sync response envelope (contract §2). */
export interface SyncResponse {
  results: SyncResult[];
  total_bars: number;
}

/** GET /api/market/history/coverage row (contract §2). */
export interface CoverageRow {
  ticker: string;
  from: string;
  to: string;
  count: number;
  source: string;
}

/** Wait for the live price stream (same pattern as the other specs). */
export async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

/**
 * GET /api/market/history/coverage → per-ticker rows. The row shape is
 * pinned by the contract; the top-level envelope is not, so accept both a
 * bare array and a {coverage: [...]} wrapper.
 */
export async function getCoverage(
  request: APIRequestContext
): Promise<CoverageRow[]> {
  const res = await request.get('/api/market/history/coverage');
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as
    | CoverageRow[]
    | { coverage?: CoverageRow[] };
  return Array.isArray(body) ? body : (body.coverage ?? []);
}

/**
 * Sync daily bars from the SAMPLE source through the cookie-path request
 * context and hand back the successful response body.
 *
 * The endpoint throttles calls closer than 10s apart with a 429 (contract
 * §2 — the guard against hammering real providers), and retried tests or
 * neighbouring specs may have synced moments ago. So this polls — the
 * expectAuditRow convention, never a bare sleep — returning sentinel
 * strings until a non-throttled 200 lands; the interval ladder crosses the
 * 10s window.
 */
export async function syncSampleHistory(
  request: APIRequestContext,
  tickers: string[]
): Promise<SyncResponse> {
  let sync: SyncResponse | null = null;
  await expect
    .poll(
      async () => {
        const res = await request.post('/api/market/history/sync', {
          data: { source: 'sample', tickers },
        });
        if (res.status() === 429) return 'throttled';
        if (!res.ok()) return `unexpected status ${res.status()}`;
        sync = (await res.json()) as SyncResponse;
        return 'ok';
      },
      {
        timeout: 60_000,
        intervals: [1_000, 3_000, 11_000],
        message: 'waiting for a non-throttled sample history sync',
      }
    )
    .toBe('ok');
  return sync!;
}

/**
 * Make sure every ticker has backtest-viable sample coverage (>= 20 daily
 * bars, the history-mode floor — contract §3), syncing from the sample
 * source only when something is missing. Idempotent: specs stay
 * self-sufficient under retries and E2E_SPECS subsets without re-hitting
 * the sync throttle.
 */
export async function ensureSampleHistory(
  request: APIRequestContext,
  tickers: string[]
): Promise<void> {
  const missing = async (): Promise<string[]> => {
    const coverage = await getCoverage(request);
    return tickers.filter(
      (ticker) =>
        (coverage.find((row) => row.ticker === ticker)?.count ?? 0) < 20
    );
  };
  if ((await missing()).length === 0) return;

  await syncSampleHistory(request, tickers);

  // The upserts are synchronous server-side, but assert the state change
  // through the public read API before any UI depends on it.
  await expect
    .poll(async () => (await missing()).length, { timeout: 15_000 })
    .toBe(0);
}

/**
 * Flip a data-source segmented switch (backtest-source /
 * strategy-bt-source) to the history side. The container testid is pinned
 * by the contract (§5); the segment is addressed by its visible copy —
 * "History"/“历史” — so one helper serves both locales.
 */
export async function switchToHistorySource(
  page: Page,
  containerTestId: string
): Promise<void> {
  const control = page.getByTestId(containerTestId);
  await expect(control).toBeVisible({ timeout: 15_000 });
  await control.getByText(/hist|历史/i).first().click();
}
