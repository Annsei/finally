/**
 * PlayerPage.test.tsx — /player?u=<id> public profile (P4 §4).
 *
 * Pure helpers: weightWidth clamp, PlayerEquity's toUtcSeconds/equityPoints.
 * Rendering:    player-empty on first-frame hydration, public profile header
 *               (rank/value/return) + weight bars + SymbolLink, BaselineSeries
 *               anchored at profile.seed_cash (US $10k / CN ¥100k), private
 *               profile empty state, own-page privacy toggle (optimistic PATCH
 *               + failure revert), 404 → not-found state. SUMMARY-ONLY: no
 *               quantity/cost/cash ever renders.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import useSWR from 'swr';
import type { PlayerProfileResponse } from '@/types/market';

jest.mock('lightweight-charts', () => {
  const mockAddSeries = jest.fn(() => ({
    update: jest.fn(),
    setData: jest.fn(),
    applyOptions: jest.fn(),
  }));
  const mockCreateChart = jest.fn(() => ({
    addSeries: mockAddSeries,
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: jest.fn(() => ({ fitContent: jest.fn() })),
  }));
  return {
    createChart: mockCreateChart,
    LineSeries: { __sentinelType: 'LineSeries' },
    AreaSeries: { __sentinelType: 'AreaSeries' },
    BaselineSeries: { __sentinelType: 'BaselineSeries' },
  };
});

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn(),
}));

jest.mock('@/components/AppShell', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
  TRADE_REVALIDATE_KEYS: [
    '/api/portfolio/',
    '/api/portfolio/trades',
    '/api/portfolio/orders?status=open',
    '/api/rules',
    '/api/watchlist/',
  ],
}));

import { useRouter } from 'next/compat/router';
import { createChart, BaselineSeries } from 'lightweight-charts';
import PlayerPage, { weightWidth } from '@/pages/player';
import { toUtcSeconds, equityPoints } from '@/components/PlayerEquity';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

const publicPlayer = (over: Partial<PlayerProfileResponse> = {}): PlayerProfileResponse => ({
  user: { id: 'fiona', name: 'Fiona', created_at: '2026-07-01T00:00:00Z' },
  public: true,
  total_value: 11250.5,
  return_pct: 12.51,
  rank: 1,
  equity_curve: [
    { time: '2026-07-06T00:00:00Z', value: 10000 },
    { time: '2026-07-06T00:00:30Z', value: 10500 },
  ],
  positions_summary: [
    { ticker: 'NVDA', weight_pct: 61.5 },
    { ticker: 'AAPL', weight_pct: 38.5 },
  ],
  ...over,
});

function mockData(opts: {
  player?: PlayerProfileResponse;
  playerError?: Error;
  meId?: string | null;
  profile?: Record<string, unknown>;
  playerMutate?: jest.Mock;
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key.startsWith('/api/players/')) {
      return {
        data: opts.player,
        error: opts.playerError,
        mutate: opts.playerMutate ?? jest.fn(),
      };
    }
    if (key === '/api/auth/me' && opts.meId) {
      return { data: { user: { id: opts.meId, name: opts.meId } }, mutate: jest.fn() };
    }
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('player helpers (P4 §4)', () => {
  it('weightWidth clamps into 0..100', () => {
    expect(weightWidth(61.5)).toBe(61.5);
    expect(weightWidth(-3)).toBe(0);
    expect(weightWidth(140)).toBe(100);
    expect(weightWidth(undefined)).toBe(0);
    expect(weightWidth(NaN)).toBe(0);
  });

  it('toUtcSeconds accepts Unix seconds, milliseconds, and ISO strings', () => {
    expect(toUtcSeconds(1_751_800_000)).toBe(1_751_800_000);
    expect(toUtcSeconds(1_751_800_000_500)).toBe(1_751_800_000); // ms heuristic
    expect(toUtcSeconds('2026-07-06T00:00:30Z')).toBe(Math.floor(Date.parse('2026-07-06T00:00:30Z') / 1000));
    expect(toUtcSeconds('nope')).toBeNull();
    expect(toUtcSeconds(null)).toBeNull();
  });

  it('equityPoints sorts ascending and dedupes same-second points keeping the LAST', () => {
    const points = equityPoints([
      { time: '2026-07-06T00:00:30Z', value: 10500 },
      { time: '2026-07-06T00:00:00Z', value: 10000 },
      { time: '2026-07-06T00:00:30.400Z', value: 10800 }, // same second — wins
    ]);
    const t = (iso: string) => Math.floor(Date.parse(iso) / 1000);
    expect(points).toEqual([
      { time: t('2026-07-06T00:00:00Z'), value: 10000 },
      { time: t('2026-07-06T00:00:30Z'), value: 10800 },
    ]);
    expect(equityPoints(undefined)).toEqual([]);
  });
});

describe('PlayerPage (P4 §4)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseRouter.mockReturnValue({ query: {} } as never);
    mockData({});
    global.fetch = jest.fn();
  });

  afterEach(() => {
    delete (global as Record<string, unknown>).fetch;
  });

  it('renders player-empty while the router query has not resolved (hydration)', () => {
    render(<PlayerPage />);
    expect(screen.getByTestId('player-empty')).toBeTruthy();
  });

  it('public profile: header shows name/rank/value/return; weights render as capped bars', () => {
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    mockData({ player: publicPlayer(), meId: 'someone-else' });
    render(<PlayerPage />);

    expect(screen.getByTestId('player-name').textContent).toBe('Fiona');
    expect(screen.getByTestId('player-rank').textContent).toBe('#1');
    expect(screen.getByTestId('player-total').textContent).toBe('$11,250.50');
    const ret = screen.getByTestId('player-return');
    expect(ret.textContent).toBe('+12.51%');
    expect(ret.className).toContain('text-terminal-up');

    const weights = screen.getByTestId('player-weights');
    expect(weights).toBeTruthy();
    const nvda = screen.getByTestId('player-weight-NVDA');
    expect(nvda.textContent).toContain('61.5%');
    expect(nvda.querySelector('[data-weight]')?.getAttribute('data-weight')).toBe('61.5');
    // codes link to the symbol page
    expect(screen.getByTestId('symbol-link-NVDA')).toBeTruthy();

    // summary only — no privacy toggle for another viewer
    expect(screen.queryByTestId('player-privacy-toggle')).toBeNull();
  });

  it('mounts the equity curve as a BaselineSeries anchored at the US seed cash', () => {
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    mockData({ player: publicPlayer() });
    render(<PlayerPage />);

    expect(screen.getByTestId('player-equity')).toBeTruthy();
    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 10000 } })
    );
    const series = chart.addSeries.mock.results[0].value as { setData: jest.Mock };
    const t = (iso: string) => Math.floor(Date.parse(iso) / 1000);
    expect(series.setData).toHaveBeenCalledWith([
      { time: t('2026-07-06T00:00:00Z'), value: 10000 },
      { time: t('2026-07-06T00:00:30Z'), value: 10500 },
    ]);
  });

  it('cn: baseline re-anchors to the ¥100k seed and the total renders with ¥', () => {
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    mockData({
      player: publicPlayer({ total_value: 112500 }),
      profile: { market: 'cn', currency_symbol: '¥', locale: 'zh-CN', up_is_red: true, seed_cash: 100000 },
    });
    render(<PlayerPage />);

    expect(screen.getByTestId('player-total').textContent).toBe('¥112,500.00');
    const chart = jest.mocked(createChart).mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 100000 } })
    );
  });

  it('private profile (another viewer) → player-private, and NO summary sections', () => {
    mockUseRouter.mockReturnValue({ query: { u: 'ghost' } } as never);
    mockData({
      player: { user: { id: 'ghost', name: 'Ghost' }, public: false },
      meId: 'someone-else',
    });
    render(<PlayerPage />);

    expect(screen.getByTestId('player-private')).toBeTruthy();
    expect(screen.queryByTestId('player-weights')).toBeNull();
    expect(screen.queryByTestId('player-equity')).toBeNull();
    expect(screen.queryByTestId('player-privacy-toggle')).toBeNull();
  });

  it('own page: privacy toggle PATCHes /api/players/me with an optimistic flip', async () => {
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    const playerMutate = jest.fn();
    mockData({ player: publicPlayer(), meId: 'fiona', playerMutate });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ public: false }),
    });
    render(<PlayerPage />);

    const toggle = screen.getByTestId('player-privacy-toggle');
    expect(toggle.textContent).toBe('Public');

    await act(async () => {
      fireEvent.click(toggle);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/players/me',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ public: false }),
      })
    );
    await waitFor(() => expect(toggle.textContent).toBe('Private'));
    expect(playerMutate).toHaveBeenCalled();
  });

  it('own PRIVATE page: toggle shows Private, summary still renders, ONE click re-opens', async () => {
    // The owner of a private profile still gets the FULL payload — the
    // backend reports the real flag (public:false + profile_public:false).
    // hasDetail keeps the summary visible; the toggle reflects the actual
    // flag so a single click sends PATCH {public:true}.
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    mockData({
      player: publicPlayer({ public: false, profile_public: false }),
      meId: 'fiona',
    });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ public: true }),
    });
    render(<PlayerPage />);

    const toggle = screen.getByTestId('player-privacy-toggle');
    expect(toggle.textContent).toBe('Private');
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    // The owner still sees their own full summary alongside the toggle.
    expect(screen.getByTestId('player-weights')).toBeTruthy();
    expect(screen.queryByTestId('player-private')).toBeNull();

    await act(async () => {
      fireEvent.click(toggle);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/players/me',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ public: true }),
      })
    );
    await waitFor(() => expect(toggle.textContent).toBe('Public'));
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
  });

  it('own page: a failed PATCH reverts the optimistic flip and surfaces the error', async () => {
    mockUseRouter.mockReturnValue({ query: { u: 'fiona' } } as never);
    mockData({ player: publicPlayer(), meId: 'fiona' });
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({}) });
    render(<PlayerPage />);

    const toggle = screen.getByTestId('player-privacy-toggle');
    await act(async () => {
      fireEvent.click(toggle);
    });

    await waitFor(() => {
      expect(screen.getByTestId('player-privacy-error')).toBeTruthy();
    });
    expect(toggle.textContent).toBe('Public'); // reverted
  });

  it('a fetch error (404) renders the not-found state', () => {
    mockUseRouter.mockReturnValue({ query: { u: 'nobody' } } as never);
    mockData({ playerError: new Error('404 Not Found') });
    render(<PlayerPage />);
    expect(screen.getByTestId('player-notfound')).toBeTruthy();
  });
});
