/**
 * RunSourceBadges.test.tsx — data-source badges on the Run Library rows
 * (`run-source-${id}`) and the /run detail header (`run-source-badge`), D1 §5.
 * Pre-D1 rows carry no source marker and must render as Simulated.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import type { BacktestRun, BacktestRunListItem, BacktestStats } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
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
}));

jest.mock('lightweight-charts', () => {
  const mockAddSeries = jest.fn().mockReturnValue({
    setData: jest.fn(),
    update: jest.fn(),
    applyOptions: jest.fn(),
  });
  const mockCreateChart = jest.fn().mockReturnValue({
    addSeries: mockAddSeries,
    remove: jest.fn(),
    applyOptions: jest.fn(),
    timeScale: jest.fn().mockReturnValue({ fitContent: jest.fn() }),
  });
  const LineSeries = { __sentinelType: 'LineSeries' };
  const BaselineSeries = { __sentinelType: 'BaselineSeries' };
  return { createChart: mockCreateChart, LineSeries, BaselineSeries };
});

import { useRouter } from 'next/compat/router';
import RunsPage, { RUNS_KEY } from '@/pages/runs';
import RunPage from '@/pages/run';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

const stats: BacktestStats = {
  total_return_pct: 4.31,
  buy_hold_return_pct: 6.02,
  max_drawdown_pct: 3.87,
  final_equity: 10431.22,
  fires: 6,
  round_trips: 6,
  win_rate: 0.67,
  avg_win: 141.02,
  avg_loss: -80.55,
  profit_factor: 2.33,
  commission_paid: 0,
  rejections: { insufficient_cash: 0 },
};

const listItem = (id: string, over: Partial<BacktestRunListItem> = {}): BacktestRunListItem => ({
  id,
  strategy_id: null,
  label: null,
  created_at: '2026-07-07T10:00:00Z',
  ticker: 'AAPL',
  days: 30,
  runs: 1,
  seed: 42,
  stats,
  ...over,
});

describe('Run Library source badges (D1 §5)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseRouter.mockReturnValue({ push: jest.fn(), query: {} } as never);
  });

  it('rows badge the stored source and default pre-D1 rows to Simulated', () => {
    mockUseSWR.mockImplementation(((key: string) => {
      if (key === RUNS_KEY) {
        return {
          data: {
            runs: [
              listItem('r-legacy'),
              listItem('r-hist', { source: 'sample' }),
            ],
          },
          mutate: jest.fn(),
        };
      }
      if (key === '/api/strategies?status=all') {
        return { data: { strategies: [] }, mutate: jest.fn() };
      }
      return { data: undefined, mutate: jest.fn() };
    }) as never);

    render(<RunsPage />);
    const legacy = screen.getByTestId('run-source-r-legacy');
    expect(legacy.textContent).toBe('Simulated');
    expect(legacy.getAttribute('data-source')).toBe('synthetic');

    const hist = screen.getByTestId('run-source-r-hist');
    expect(hist.textContent).toBe('Sample');
    expect(hist.getAttribute('data-source')).toBe('sample');
  });
});

describe('/run detail source badge (D1 §5)', () => {
  const runFixture = (configOver: Record<string, unknown> = {}): BacktestRun => ({
    id: 'r1',
    strategy_id: null,
    label: null,
    created_at: '2026-07-07T10:00:00Z',
    config: {
      ticker: 'AAPL',
      trigger_type: 'day_change_pct_below',
      threshold: -2,
      side: 'buy',
      quantity: 5,
      take_profit_pct: 5,
      stop_loss_pct: 3,
      days: 30,
      runs: 1,
      seed: 42,
      commission_bps: 0,
      anchor_price: 190,
      ...configOver,
    },
    stats,
    equity_curve: [
      { time: 1_751_000_000, value: 10000 },
      { time: 1_751_000_060, value: 10431.22 },
    ],
    baseline_curve: [
      { time: 1_751_000_000, value: 10000 },
      { time: 1_751_000_060, value: 10602 },
    ],
    trades: [],
    runs_summary: null,
  });

  function mockRun(run: BacktestRun) {
    mockUseSWR.mockImplementation(((key: string) => {
      if (key === '/api/backtest/runs/r1') {
        return { data: { run }, mutate: jest.fn() };
      }
      return { data: undefined, mutate: jest.fn() };
    }) as never);
  }

  beforeEach(() => {
    jest.clearAllMocks();
    mockUseRouter.mockReturnValue({ query: { id: 'r1' } } as never);
  });

  it('renders the history badge with the evaluated date range', () => {
    mockRun(
      runFixture({
        seed: null,
        source: 'yfinance',
        date_range: { from: '2026-01-02', to: '2026-07-01' },
      })
    );
    render(<RunPage />);
    const badge = screen.getByTestId('run-source-badge');
    expect(badge.getAttribute('data-source')).toBe('yfinance');
    expect(badge.textContent).toContain('yfinance');
    expect(badge.textContent).toContain('2026-01-02 → 2026-07-01');
  });

  it('legacy configs (no marker) badge as Simulated without a range', () => {
    mockRun(runFixture());
    render(<RunPage />);
    const badge = screen.getByTestId('run-source-badge');
    expect(badge.getAttribute('data-source')).toBe('synthetic');
    expect(badge.textContent).toBe('Simulated');
  });

  it('strategy-shaped configs keep their engine discriminator out of the badge', () => {
    // config.source === "strategy" is run_backtest's shape marker, not a data
    // source — without a date_range it reads as the synthetic path.
    mockRun(runFixture({ source: 'strategy' }));
    render(<RunPage />);
    expect(screen.getByTestId('run-source-badge').getAttribute('data-source')).toBe('synthetic');
  });
});
