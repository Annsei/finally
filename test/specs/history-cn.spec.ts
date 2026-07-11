import { test, expect } from '@playwright/test';
import {
  ensureSampleHistory,
  getCoverage,
  switchToHistorySource,
  syncSampleHistory,
  waitForConnected,
} from './history-helpers';

/**
 * A-share (CN) history data layer E2E (D1_HISTORY_CONTRACT.md §6).
 *
 * Runs ONLY in the CN harness: the filename matches the CN project's
 * testMatch (/cn\.spec\.ts/) and is excluded from the default US run by the
 * e2e project's testIgnore — same zero-config split as the other *-cn
 * specs. Launched via docker-compose.cn.test.yml (FINALLY_MARKET=cn,
 * LLM_MOCK=true).
 *
 * CN acceptance (contract §6: 中文文案 + cn sample 同步):
 *   (a) a cn sample sync (600519, request context, source=sample) lands in
 *       coverage, and the /market history-coverage card renders the range
 *       with Chinese copy and a Chinese Guest-usable sync button
 *   (b) the Backtest tab's data-source segments read 模拟 | 历史; on the
 *       history side the days label reads 交易日 and a 600519 run
 *       completes with the source badge
 *
 * ZERO external network: syncs post the explicit `sample` source; the UI
 * sync button (source=auto — would try a real provider) is never clicked.
 */

const CJK = /[一-鿿]/;

/** CN universe fixture ticker (600519 贵州茅台 — profile seed). */
const TICKER = '600519';
// Scenario (b) trades this one instead: one lot of 600519 (~¥170k) exceeds
// the ¥100k CN seed cash, so a 600519 history run can never fill — 600036's
// lot (~¥3.4k) exercises the real fill/lot/fee path on the sample bars.
const BT_TICKER = '600036';

test.describe('A-share (CN) history data layer', () => {
  test('(a) cn sample sync fills coverage and the /market card renders it in Chinese', async ({
    page,
    request,
  }) => {
    // Cookie-path sync with the explicit sample source (429-tolerant
    // polling in the helper — expectAuditRow convention, no bare sleeps).
    const sync = await syncSampleHistory(request, [TICKER]);
    expect(sync.total_bars).toBeGreaterThan(0);
    const result = sync.results.find((r) => r.ticker === TICKER);
    expect(result, `sync result for ${TICKER}`).toBeTruthy();
    expect(result!.source).toBe('sample');
    expect(result!.error ?? null).toBeNull();
    expect(result!.bars).toBeGreaterThan(0);

    // Coverage read-back (§2): a dated interval with the persisted count.
    const coverage = await getCoverage(request);
    const row = coverage.find((c) => c.ticker === TICKER);
    expect(row, `coverage row for ${TICKER}`).toBeTruthy();
    expect(row!.count).toBeGreaterThanOrEqual(20);
    expect(row!.source).toBe('sample');
    expect(row!.from).toMatch(/^\d{4}-\d{2}-\d{2}/);
    expect(row!.to).toMatch(/^\d{4}-\d{2}-\d{2}/);

    // The /market data-status card renders the ticker's range with Chinese
    // copy (history.* zh keys) and the Guest-usable sync button, also in
    // Chinese. Never clicked — the button posts source=auto.
    await page.goto('/market/');
    await waitForConnected(page);
    const card = page.getByTestId('history-coverage');
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText(TICKER, { timeout: 15_000 });
    await expect(card).toContainText(/\d{4}/);
    await expect(card).toContainText(CJK);

    const button = page.getByTestId('history-sync-button');
    await expect(button).toBeVisible({ timeout: 15_000 });
    await expect(button).toBeEnabled();
    await expect(button).toContainText(CJK);
  });

  test('(b) the Backtest tab history mode is Chinese (模拟|历史, 交易日) and completes a 600519 run', async ({
    page,
    request,
  }) => {
    // Self-sufficient under retries/subsets: only syncs when bars miss.
    await ensureSampleHistory(request, [BT_TICKER]);

    await page.goto('/');
    await waitForConnected(page);
    await page.getByTestId('tab-backtest').click();
    await expect(page.getByTestId('backtest-run')).toBeVisible();

    // The segmented data-source switch carries the zh segment copy pinned
    // by the contract (§5: 模拟 | 历史).
    const source = page.getByTestId('backtest-source');
    await expect(source).toBeVisible({ timeout: 15_000 });
    await expect(source).toContainText('模拟');
    await expect(source).toContainText('历史');

    // Affordable CN universe ticker (one lot within seed cash) so the run
    // actually fills; the form default qty is one lot (100) and 30 days
    // sits inside the 20..750 clamp.
    await page.locator('#bt-ticker').fill(BT_TICKER);
    await switchToHistorySource(page, 'backtest-source');

    // History mode relabels the horizon in trading days (§5: 交易日).
    await expect(page.locator('label[for="bt-days"]')).toContainText('交易日');

    // The run completes on the sample daily bars: stats render and the
    // stats area shows the source badge — sample-backed history, so the
    // badge copy is the sample/history label, never the synthetic one.
    await page.getByTestId('backtest-run').click();
    await expect(page.getByTestId('backtest-stats')).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId('backtest-return')).toContainText('%');
    const badge = page.getByTestId('backtest-source-badge');
    await expect(badge).toBeVisible({ timeout: 15_000 });
    await expect(badge).toContainText(/样本|历史|sample|hist/i);
  });
});
