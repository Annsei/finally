import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { flattenPosition, getPortfolio, trade } from './helpers';

/**
 * P4 polish E2E (P4_POLISH_CONTRACT.md §6, US project).
 *
 * Covers the contract scenarios:
 *   ① /market sentiment dial: GET /api/market/sentiment returns the pinned
 *     {score, label, axes, sample_size} shape and the market-sentiment panel
 *     renders the score digits plus a five-level label
 *   ② trade journal calendar: after a buy→sell round trip with a nonzero
 *     realized P&L, today's journal-cal-day-${YYYY-MM-DD} cell renders with a
 *     non-transparent direction-color background (0-trade days are
 *     transparent by contract §3)
 *   ③ leaderboard name cell links to /player?u=… where the player-equity
 *     curve (lightweight-charts canvas) renders
 *   ④ privacy toggle: owner PATCH /api/players/me {public:false} → an
 *     anonymous outsider's GET /api/players/{id} returns only the
 *     {user, public:false} envelope (no equity/totals/positions); flipping
 *     back re-exposes the summary — which never leaks qty/cost/cash (§4)
 *   ⑤ /market correlation heatmap: once ≥2 tickers have ≥10 completed
 *     one-minute bars, the API returns an NxN matrix (diagonal 1.0) and the
 *     market-correlation grid renders market-corr-${A}-${B} cells with the
 *     "A×B r=…" hover title. Bars only accumulate from app boot (~11 min
 *     worst case), so this test polls the API with an extended budget and
 *     runs LAST in this file to maximize elapsed uptime. No bare sleeps —
 *     expect.poll and reload-polling throughout (P2/P3 expectAuditRow
 *     convention).
 *
 * Testid contract (P4_POLISH_CONTRACT.md §1-§4): market-sentiment,
 * market-correlation, market-corr-${A}-${B}, journal-calendar,
 * journal-cal-day-${YYYY-MM-DD}, player-link-${user_id}, player-equity.
 * connection-status / leaderboard-table come from the existing contract.
 */

/** The five sentiment label keys (contract §1; en dict renders these words). */
const SENTIMENT_LABEL_RE = /(frozen|cool|neutral|active|hot)/i;

/** Wait for the live price stream (same pattern as the other specs). */
async function waitForConnected(page: Page): Promise<void> {
  await expect(page.getByTestId('connection-status')).toHaveAttribute(
    'data-state',
    'connected',
    { timeout: 20_000 }
  );
}

/**
 * Today's calendar-cell key in local time — the calendar aggregates trades by
 * the browser's local day (contract §3), and the Playwright runner and
 * Chromium share the container's timezone.
 */
function localDayKey(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

test.describe('P4 polish — sentiment, calendar, player pages, correlation', () => {
  test('① /market sentiment dial shows a 0-100 score and a five-level label', async ({
    page,
    request,
  }) => {
    // API contract first (request-context assertion): the pinned shape.
    const res = await request.get('/api/market/sentiment');
    expect(res.status()).toBe(200);
    const body = (await res.json()) as {
      score: number;
      label: string;
      axes: Record<string, number>;
      sample_size: number;
    };
    expect(Number.isInteger(body.score)).toBe(true);
    expect(body.score).toBeGreaterThanOrEqual(0);
    expect(body.score).toBeLessThanOrEqual(100);
    expect(['frozen', 'cool', 'neutral', 'active', 'hot']).toContain(body.label);
    for (const axis of ['breadth', 'volatility', 'volume'] as const) {
      expect(body.axes[axis]).toBeGreaterThanOrEqual(0);
      expect(body.axes[axis]).toBeLessThanOrEqual(100);
    }
    // The US universe streams 10 tickers from boot — never the <2 fallback.
    expect(body.sample_size).toBeGreaterThanOrEqual(2);

    // The dashboard renders above/beside the grid (contract §1) with the
    // score digits and the i18n label (SWR 10s — the live score may have
    // ticked since the API call, so assert digits, not the exact value).
    await page.goto('/market/');
    await waitForConnected(page);
    const dial = page.getByTestId('market-sentiment');
    await expect(dial).toBeVisible({ timeout: 15_000 });
    await expect(dial).toContainText(/\d/, { timeout: 15_000 });
    await expect(dial).toContainText(SENTIMENT_LABEL_RE, { timeout: 15_000 });
  });

  test("② after a round-trip trade, today's calendar cell shows a P&L color", async ({
    page,
    request,
  }) => {
    const TICKER = 'AAPL';
    const QTY = 5;
    // Clean slate (retried runs / earlier specs may have left a position).
    await flattenPosition(request, TICKER);

    // Buy, wait for the live price to move off the entry, then sell — this
    // books a nonzero realized P&L for today. A zero-P&L day would be
    // indistinguishable from the transparent 0-trade state (contract §3).
    const buyRes = await trade(request, TICKER, 'buy', QTY);
    expect(buyRes.ok()).toBeTruthy();
    await expect
      .poll(
        async () => {
          const portfolio = await getPortfolio(request);
          const position = portfolio.positions.find((p) => p.ticker === TICKER);
          return position ? Math.abs(position.current_price - position.avg_cost) : 0;
        },
        { timeout: 30_000, message: 'waiting for the price to move off the entry' }
      )
      .toBeGreaterThan(0);
    const sellRes = await trade(request, TICKER, 'sell', QTY);
    expect(sellRes.ok()).toBeTruthy();

    // The blotter confirms the sell booked a nonzero realized P&L today.
    const tradesRes = await request.get(`/api/portfolio/trades?ticker=${TICKER}&limit=5`);
    expect(tradesRes.ok()).toBeTruthy();
    const { trades: blotter } = (await tradesRes.json()) as {
      trades: { side: string; realized_pnl: number | null }[];
    };
    const sell = blotter.find((t) => t.side === 'sell');
    expect(sell).toBeTruthy();
    expect(Math.abs(sell!.realized_pnl ?? 0)).toBeGreaterThan(0);

    // The journal page fetches its trades once per mount, so reload-poll
    // (expectAuditRow convention — assert across remounts, never sleep)
    // until today's cell renders with a non-transparent P&L background.
    const cell = page.getByTestId(`journal-cal-day-${localDayKey()}`);
    const attempts = 4;
    for (let attempt = 1; ; attempt++) {
      await page.goto('/journal/');
      await waitForConnected(page);
      try {
        await expect(page.getByTestId('journal-calendar')).toBeVisible({
          timeout: 10_000,
        });
        await expect(cell).toBeVisible({ timeout: 5_000 });
        // 盈亏色: nonzero realized P&L → direction-color mix, never the
        // transparent 0-trade background.
        const bg = await cell.evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(bg).not.toBe('transparent');
        expect(bg).not.toBe('rgba(0, 0, 0, 0)');
        break;
      } catch (err) {
        if (attempt >= attempts) throw err;
        // Reload-poll: the next iteration remounts the page and refetches.
      }
    }
  });

  test('③ leaderboard name links to /player where the equity curve renders', async ({
    page,
    request,
  }) => {
    // Guarantee at least one snapshot for the default user's equity curve —
    // a snapshot is recorded immediately after every trade (PLAN.md §7).
    const TICKER = 'MSFT';
    await flattenPosition(request, TICKER);
    const buyRes = await trade(request, TICKER, 'buy', 1);
    expect(buyRes.ok()).toBeTruthy();
    await trade(request, TICKER, 'sell', 1);
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/portfolio/history');
          if (!res.ok()) return 0;
          const { snapshots } = (await res.json()) as { snapshots: unknown[] };
          return snapshots.length;
        },
        { timeout: 15_000 }
      )
      .toBeGreaterThan(0);

    await page.goto('/arena/');
    await waitForConnected(page);
    await expect(page.getByTestId('leaderboard-table')).toBeVisible({
      timeout: 15_000,
    });

    // The default user's name cell is wrapped in a Link (contract §4) and
    // keeps the original text node ("Guest" for the anonymous default user).
    const link = page.getByTestId('player-link-default');
    await expect(link).toBeVisible({ timeout: 15_000 });
    await expect(link).toContainText('Guest');
    await link.click();

    // /player?u=<id> query mode, same as /symbol?c=… (contract §4).
    await expect(page).toHaveURL(/\/player\/?\?(.*&)?u=default/, {
      timeout: 15_000,
    });

    // The equity curve is canvas-based (lightweight-charts BaselineSeries) —
    // a canvas must render inside player-equity.
    const equity = page.getByTestId('player-equity');
    await expect(equity).toBeVisible({ timeout: 15_000 });
    await expect(equity.locator('canvas').first()).toBeVisible({ timeout: 15_000 });
  });

  test('④ privacy toggle: PATCH /api/players/me hides the profile from outsiders', async ({
    page,
    request,
  }) => {
    // A named identity — the session cookie lands in this request context's
    // jar, so subsequent `request` calls are the profile owner (cookie 判定).
    const login = await request.post('/api/auth/login', {
      data: { name: 'E2E-P4-Priv' },
    });
    expect(login.ok()).toBeTruthy();
    const { user } = (await login.json()) as { user: { id: string } };

    try {
      // Owner flips the profile private.
      const patch = await request.patch('/api/players/me', {
        data: { public: false },
      });
      expect(patch.ok()).toBeTruthy();
      expect(((await patch.json()) as { public: boolean }).public).toBe(false);

      // An anonymous outsider (page.request shares the browser context's
      // cookie jar, which never logged in) now sees ONLY the
      // {user, public:false} envelope — no curve, no totals (contract §4).
      const res = await page.request.get(`/api/players/${user.id}`);
      expect(res.status()).toBe(200);
      const body = (await res.json()) as Record<string, unknown>;
      expect(body.public).toBe(false);
      expect((body.user as { id: string }).id).toBe(user.id);
      expect(body).not.toHaveProperty('equity_curve');
      expect(body).not.toHaveProperty('total_value');
      expect(body).not.toHaveProperty('positions_summary');
    } finally {
      // Re-open the profile so retries and later runs see the default state.
      const reopen = await request.patch('/api/players/me', {
        data: { public: true },
      });
      expect(reopen.ok()).toBeTruthy();
    }

    // Public again: outsiders get the summary shape — which must never leak
    // quantities, costs, or cash (概要-only invariant, contract §4).
    const pub = await page.request.get(`/api/players/${user.id}`);
    expect(pub.status()).toBe(200);
    const pubBody = (await pub.json()) as Record<string, unknown>;
    expect(pubBody.public).toBe(true);
    expect(typeof pubBody.total_value).toBe('number');
    expect(Array.isArray(pubBody.equity_curve)).toBe(true);
    const flat = JSON.stringify(pubBody);
    expect(flat).not.toContain('avg_cost');
    expect(flat).not.toContain('cash_balance');
    expect(flat).not.toContain('"quantity"');
  });

  test('⑤ /market correlation heatmap renders NxN cells once 1m bars accumulate', async ({
    page,
    request,
  }) => {
    // Tickers qualify only with >=10 COMPLETED one-minute bars
    // (aggregate_minute_bars drops the forming bucket), which accumulate
    // solely from app boot — ~11 minutes worst case on a fresh container.
    // Extend this test's budget and poll the API; no bare sleeps.
    test.setTimeout(900_000);

    interface Corr {
      tickers: string[];
      sectors: Record<string, string>;
      matrix: number[][];
      minutes: number;
    }
    let corr: Corr = { tickers: [], sectors: {}, matrix: [], minutes: 0 };
    await expect
      .poll(
        async () => {
          const res = await request.get('/api/market/correlation');
          if (!res.ok()) return -1;
          corr = (await res.json()) as Corr;
          return corr.tickers.length;
        },
        {
          timeout: 840_000,
          intervals: [10_000],
          message: 'waiting for >=2 tickers to reach 10 completed 1m bars',
        }
      )
      .toBeGreaterThanOrEqual(2);

    // Pinned response shape (contract §2): default 30-minute window, square
    // matrix, self-correlation 1.0 on the diagonal, values within [-1, 1],
    // and a sector for every included ticker.
    expect(corr.minutes).toBe(30);
    const n = corr.tickers.length;
    expect(corr.matrix).toHaveLength(n);
    for (const row of corr.matrix) {
      expect(row).toHaveLength(n);
      for (const r of row) {
        expect(r).toBeGreaterThanOrEqual(-1);
        expect(r).toBeLessThanOrEqual(1);
      }
    }
    for (let i = 0; i < n; i++) {
      expect(corr.matrix[i][i]).toBeCloseTo(1.0, 5);
    }
    for (const ticker of corr.tickers) {
      expect(typeof corr.sectors[ticker]).toBe('string');
    }

    // The heatmap grid renders a cell per pair with the pinned hover title.
    const [a, b] = corr.tickers;
    await page.goto('/market/');
    await waitForConnected(page);
    await expect(page.getByTestId('market-correlation')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId(`market-corr-${a}-${a}`)).toBeVisible({
      timeout: 15_000,
    });
    const cell = page.getByTestId(`market-corr-${a}-${b}`);
    await expect(cell).toBeVisible({ timeout: 15_000 });
    await expect(cell).toHaveAttribute('title', /r=/);
  });
});
