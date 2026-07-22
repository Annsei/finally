import { test, expect } from '@playwright/test';
import { flattenPosition, trade } from './helpers';
import {
  ensureSampleHistory,
  getCoverage,
  waitForConnected,
} from './history-helpers';
import {
  createCompetitionViaUI,
  expectCompRow,
  getBoard,
  listCompetitions,
  resolveCompetitionByName,
} from './arena-helpers';

/**
 * Timed private competitions + portfolio VaR/beta E2E
 * (D2_LIVE_ARENA_CONTRACT.md §6, US project).
 *
 * Covers the three contract scenarios:
 *   ① Guest creates a competition through the /arena comp-create form → the
 *     code renders, the countdown ticks (per-second local decrement) and the
 *     expanded comp-board lists the auto-joined creator
 *   ② Guest creates cookie-path (201 {competition} + code); a second
 *     identity (separate request context, POST /api/auth/login as Bob)
 *     joins by code — repeat join is idempotent 200 → the board carries
 *     two ranked members, the creator's mine-scope row shows the code and
 *     the joiner's hides it (creator-only per §3)
 *   ③ risk analytics chain: with a held position but no daily bars,
 *     var_95_pct/beta are null (risk_window_bars 0) and the Analytics tab
 *     shows the em-dash cards with the sync hint; after a sample history
 *     sync for the whole default universe (the §4 equal-weight benchmark)
 *     both keys turn numeric and the cards render digits
 *
 * ZERO external network (contract core invariant): no FINALLY_LIVE_SOURCE,
 * every history sync posts the explicit `sample` source through the
 * 429-tolerant helper. Waiting is expect.poll / expect timeouts /
 * reload-polling — never a bare sleep.
 *
 * Testid contract (§5): comp-create, comp-name, comp-hours, comp-join-code,
 * comp-join, comp-row-${id}, comp-countdown-${id}, comp-board-${id},
 * analytics-var, analytics-beta, analytics-risk-hint. connection-status /
 * tab-analytics come from the existing contract.
 *
 * Order note: this file sorts first in the e2e project. It leaves no open
 * position behind (③ flattens its AAPL fixture); the bob user and the
 * competitions it creates persist, which later specs tolerate by design
 * (they flatten/create their own fixtures — p4 precedent).
 */

/** The full default US watchlist — the §4 market benchmark universe. */
const UNIVERSE = [
  'AAPL',
  'GOOGL',
  'MSFT',
  'AMZN',
  'TSLA',
  'NVDA',
  'META',
  'JPM',
  'V',
  'NFLX',
];

/** Competition join codes: 6 chars from A-Z2-9 (§3, no 0/1/O/I). */
const CODE_RE = /^[A-Z2-9]{6}$/;

test.describe('competitions + portfolio risk analytics', () => {
  test('① Guest creates a competition on /arena and the board lists the creator', async ({
    page,
    request,
  }) => {
    const name = `E2E Comp A ${Date.now()}`;
    await page.goto('/arena/');
    await waitForConnected(page);
    await createCompetitionViaUI(page, name, 2);

    // API contract (§3): the creator's mine-scope row carries the code, a
    // running status (starts_at = create), the creator auto-membership and
    // an ends_at exactly `hours` after starts_at.
    const comp = await resolveCompetitionByName(request, name);
    expect(comp.code ?? '').toMatch(CODE_RE);
    expect(comp.status).toBe('running');
    expect(comp.member_count).toBe(1);
    const startsMs = new Date(comp.starts_at).getTime();
    const endsMs = new Date(comp.ends_at).getTime();
    expect(Math.abs(endsMs - startsMs - 2 * 3_600_000)).toBeLessThanOrEqual(
      1_000
    );

    // 创建成功展示 code (§5): the exact code from the API is on the page.
    await expect(page.getByText(comp.code!).first()).toBeVisible({
      timeout: 15_000,
    });

    // The competition row renders with its per-second local countdown —
    // digits that change within a few seconds (no bare sleep: boolean poll).
    const row = await expectCompRow(page, comp.id);
    const countdown = page.getByTestId(`comp-countdown-${comp.id}`);
    await expect(countdown).toBeVisible({ timeout: 15_000 });
    await expect(countdown).toContainText(/\d/);
    const before = (await countdown.textContent()) ?? '';
    await expect
      .poll(
        async () => ((await countdown.textContent()) ?? '') !== before,
        { timeout: 10_000, message: 'waiting for the countdown to tick' }
      )
      .toBe(true);

    // Click to expand: the board lists the auto-joined creator (the
    // anonymous default user renders as "Guest") with a return % column.
    await row.click();
    const board = page.getByTestId(`comp-board-${comp.id}`);
    await expect(board).toBeVisible({ timeout: 15_000 });
    await expect(board).toContainText('Guest', { timeout: 20_000 });
    await expect(board).toContainText('%');
  });

  test('② Bob joins by code from a second request context and the board ranks two members', async ({
    page,
    request,
    playwright,
    baseURL,
  }) => {
    const name = `E2E Comp B ${Date.now()}`;

    // Guest creates cookie-path (§3: creation is cookie-only; the anonymous
    // request context is the default user) → 201 {competition} + code.
    const createRes = await request.post('/api/competitions', {
      data: { name, hours: 2 },
    });
    expect(createRes.status()).toBe(201);
    const { competition } = (await createRes.json()) as {
      competition: { id: string; code: string };
    };
    expect(competition.code).toMatch(CODE_RE);

    // Second identity (§6): a separate cookie jar logged in as Bob joins
    // with the code; a repeat join is idempotent 200 (§3).
    const bob = await playwright.request.newContext({
      baseURL: baseURL ?? 'http://localhost:8000',
    });
    try {
      const login = await bob.post('/api/auth/login', {
        data: { name: 'Bob' },
      });
      expect(login.ok()).toBeTruthy();

      const join = await bob.post('/api/competitions/join', {
        data: { code: competition.code },
      });
      expect(join.status()).toBe(200);
      const rejoin = await bob.post('/api/competitions/join', {
        data: { code: competition.code },
      });
      expect(rejoin.status()).toBe(200);

      // API board (§3): two members, ranks {1,2}, the pinned row shape.
      // Rank↔member mapping is not asserted — both sit at ~0% return and
      // the tie order is joined_at, a pytest-scope semantic.
      const board = await getBoard(request, competition.id);
      expect(board).toHaveLength(2);
      expect(board.map((r) => r.rank).sort((a, b) => a - b)).toEqual([1, 2]);
      const ids = board.map((r) => r.user_id);
      expect(ids).toContain('default');
      expect(ids).toContain('bob');
      for (const member of board) {
        expect(typeof member.name).toBe('string');
        expect(typeof member.baseline_value).toBe('number');
        expect(typeof member.value).toBe('number');
        expect(typeof member.return_pct).toBe('number');
      }

      // Code visibility (§3: code 仅 mine 且本人创建): the creator's
      // mine-scope row carries the code, the joiner's row does not — and
      // both count the two members (joined comps land in mine scope).
      const guestMine = (await listCompetitions(request, 'mine')).find(
        (c) => c.id === competition.id
      );
      expect(guestMine, "competition in creator's scope=mine").toBeTruthy();
      expect(guestMine!.member_count).toBe(2);
      expect(guestMine!.code ?? '').toMatch(CODE_RE);
      const bobMine = (await listCompetitions(bob, 'mine')).find(
        (c) => c.id === competition.id
      );
      expect(bobMine, "joined competition in Bob's scope=mine").toBeTruthy();
      expect(bobMine!.code ?? null).toBeNull();

      // UI: Guest's /arena lists the joined competition; the expanded
      // board renders both ranked members (Bob + Guest).
      await page.goto('/arena/');
      await waitForConnected(page);
      const row = await expectCompRow(page, competition.id);
      await row.click();
      const boardEl = page.getByTestId(`comp-board-${competition.id}`);
      await expect(boardEl).toBeVisible({ timeout: 15_000 });
      await expect(boardEl).toContainText('Bob', { timeout: 20_000 });
      await expect(boardEl).toContainText('Guest', { timeout: 20_000 });
    } finally {
      await bob.dispose();
    }
  });

  test('③ VaR/beta: null without history, numeric after a sample universe sync', async ({
    page,
    request,
  }) => {
    const TICKER = 'AAPL';

    // VaR/beta require a live position (§4) — hold a small AAPL lot.
    await flattenPosition(request, TICKER);
    const buy = await trade(request, TICKER, 'buy', 3);
    expect(buy.ok()).toBeTruthy();

    const readRisk = async () => {
      const res = await request.get('/api/portfolio/analytics');
      expect(res.ok()).toBeTruthy();
      return (await res.json()) as {
        var_95_pct: number | null;
        beta: number | null;
        risk_window_bars: number;
        total_trades: number;
      };
    };

    // Null phase — only while the held ticker has no usable daily bars
    // (fresh DB). Retries / warm DBs skip it (bars survive); the contract
    // accepts either the full null→sync→numbers chain or numbers directly.
    const covered =
      (await getCoverage(request)).find((c) => c.ticker === TICKER)?.count ??
      0;
    if (covered < 20) {
      const risk = await readRisk();
      expect(risk.var_95_pct).toBeNull();
      expect(risk.beta).toBeNull();
      expect(risk.risk_window_bars).toBe(0);

      // Analytics tab: em-dash cards + the "sync history first" hint (§5).
      await page.goto('/');
      await waitForConnected(page);
      await page.getByTestId('tab-analytics').click();
      const varCard = page.getByTestId('analytics-var');
      await expect(varCard).toBeVisible({ timeout: 15_000 });
      await expect(varCard).toContainText('—');
      await expect(page.getByTestId('analytics-beta')).toContainText('—');
      await expect(page.getByTestId('analytics-risk-hint')).toBeVisible();
    }

    // Sample sync for the whole default universe — the §4 market benchmark
    // is the universe equal-weight daily return. Zero external network:
    // the helper posts the explicit sample source and rides out the 429
    // sync throttle by polling.
    await ensureSampleHistory(request, UNIVERSE);

    // API: the additive keys turn numeric within sane bounds, the window
    // sits in the ≤60-bar clamp (≥20 or it would be null), and an existing
    // analytics key is still present (additive contract, §4).
    let risk = await readRisk();
    await expect
      .poll(
        async () => {
          risk = await readRisk();
          return risk.var_95_pct !== null && risk.beta !== null;
        },
        { timeout: 20_000, message: 'waiting for VaR/beta after the sync' }
      )
      .toBe(true);
    expect(typeof risk.var_95_pct).toBe('number');
    expect(typeof risk.beta).toBe('number');
    expect(Math.abs(risk.var_95_pct!)).toBeLessThan(50);
    expect(Math.abs(risk.beta!)).toBeLessThan(10);
    expect(risk.risk_window_bars).toBeGreaterThanOrEqual(20);
    expect(risk.risk_window_bars).toBeLessThanOrEqual(60);
    expect(typeof risk.total_trades).toBe('number');

    // UI: both cards render digits instead of the em-dash placeholder.
    await page.goto('/');
    await waitForConnected(page);
    await page.getByTestId('tab-analytics').click();
    const varCard = page.getByTestId('analytics-var');
    await expect(varCard).toBeVisible({ timeout: 15_000 });
    await expect(varCard).toContainText(/\d/, { timeout: 15_000 });
    await expect(varCard).not.toContainText('—');
    const betaCard = page.getByTestId('analytics-beta');
    await expect(betaCard).toContainText(/\d/);
    await expect(betaCard).not.toContainText('—');
    // The sync hint is the null-state affordance — gone once values render.
    await expect(page.getByTestId('analytics-risk-hint')).not.toBeVisible();

    // Leave the shared portfolio flat for the trade-oriented specs.
    await flattenPosition(request, TICKER);
  });
});
