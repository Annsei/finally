/**
 * RunDetailPage.test.tsx — /run?id=X persisted-run detail (P2 §8).
 *
 * run-detail assembles the extracted backtest components verbatim:
 * StatsGrid + EquityChart + RunsSummaryStrip (runs > 1 only) + TradesBlotter,
 * plus back-links. Hydration shows run-empty; a fetch error shows not-found.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import type { BacktestRun, BacktestStats } from '@/types/market';

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

import { createChart, BaselineSeries } from 'lightweight-charts';
import { useRouter } from 'next/compat/router';
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

const runFixture = (over: Partial<BacktestRun> = {}): BacktestRun => ({
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
  trades: [
    { time: 1_751_000_000, side: 'buy', price: 186.1, quantity: 5, reason: 'trigger', pnl: null },
    {
      time: 1_751_000_060,
      side: 'sell',
      price: 195.4,
      quantity: 5,
      reason: 'take_profit',
      pnl: 46.5,
    },
  ],
  runs_summary: null,
  ...over,
});

function mockData(opts: {
  run?: BacktestRun;
  error?: boolean;
  profile?: Record<string, unknown>;
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/backtest/runs/r1') {
      if (opts.error) return { data: undefined, error: new Error('404'), mutate: jest.fn() };
      return { data: opts.run ? { run: opts.run } : undefined, mutate: jest.fn() };
    }
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('RunPage (P2 §8)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseRouter.mockReturnValue({ query: { id: 'r1' } } as never);
    mockData({ run: runFixture() });
  });

  it('renders the run-empty hydration placeholder until the query resolves', () => {
    mockUseRouter.mockReturnValue({ query: {} } as never);
    render(<RunPage />);
    expect(screen.getByTestId('run-empty')).toBeInTheDocument();
    expect(screen.getByTestId('run-empty').textContent).toBe('No run selected.');
    expect(screen.queryByTestId('run-detail')).toBeNull();
  });

  it('shows not-found when the run fetch errors', () => {
    mockData({ error: true });
    render(<RunPage />);
    expect(screen.getByText('Run not found.')).toBeInTheDocument();
    expect(screen.queryByTestId('run-detail')).toBeNull();
  });

  it('assembles the full composition: stats grid, equity chart, trades blotter', () => {
    render(<RunPage />);
    const detail = screen.getByTestId('run-detail');

    // StatsGrid (extracted component, testids unchanged)
    expect(screen.getByTestId('backtest-return').textContent).toBe('+4.31%');
    expect(detail.textContent).toContain('67%'); // win rate card

    // EquityChart mounts with both series and the $10k base value
    expect(screen.getByTestId('backtest-chart')).toBeInTheDocument();
    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 10000 } })
    );
    const equitySeries = (
      chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>
    ).value;
    expect(equitySeries.setData).toHaveBeenCalledWith([
      { time: 1_751_000_000, value: 10000 },
      { time: 1_751_000_060, value: 10431.22 },
    ]);

    // TradesBlotter
    expect(screen.getByTestId('backtest-trades').textContent).toContain('take profit');

    // Header carries the symbol link, the config line, and the library link
    expect(screen.getByTestId('symbol-link-AAPL')).toBeInTheDocument();
    expect(detail.textContent).toContain('seed 42');
    expect(screen.getByTestId('run-back-to-runs')).toBeInTheDocument();
    // no strategy attribution → no strategy back-link
    expect(screen.queryByTestId('run-back-to-strategy')).toBeNull();
    // single run → no Monte Carlo strip
    expect(screen.queryByTestId('backtest-runs-summary')).toBeNull();
  });

  it('cn: the page threads currency_symbol/locale into StatsGrid and TradesBlotter', () => {
    mockData({
      run: runFixture({ config: { ...runFixture().config, ticker: '600519' } }),
      profile: {
        market: 'cn',
        currency_symbol: '¥',
        locale: 'zh-CN',
        lot_size: 100,
        up_is_red: true,
        names: { '600519': '贵州茅台' },
        price_limit_pct: {},
      },
    });
    render(<RunPage />);
    // StatsGrid final equity in ¥ (locale grouping identical for zh-CN)
    expect(screen.getByText('¥10,431.22')).toBeInTheDocument();
    // TradesBlotter prices in ¥, dates via the zh-CN locale
    const blotter = screen.getByTestId('backtest-trades');
    expect(blotter.textContent).toContain('¥186.10');
    expect(blotter.textContent).not.toContain('$');
    expect(blotter.textContent).toContain(
      new Date(1_751_000_000 * 1000).toLocaleDateString('zh-CN', {
        month: 'short',
        day: 'numeric',
      })
    );
  });

  it('renders the Monte Carlo strip, label chip, and strategy back-link when present', () => {
    mockData({
      run: runFixture({
        strategy_id: 'st-1',
        label: 'tuned',
        runs_summary: {
          runs: 30,
          median_return_pct: 3.1,
          p05_return_pct: -6.2,
          p95_return_pct: 14.8,
          positive_share: 0.7,
          median_max_drawdown_pct: 4.4,
        },
      }),
    });
    render(<RunPage />);
    expect(screen.getByTestId('run-label').textContent).toBe('tuned');
    const strip = screen.getByTestId('backtest-runs-summary');
    expect(strip.textContent).toContain('30 runs');
    expect(strip.textContent).toContain('+3.10%');
    const back = screen.getByTestId('run-back-to-strategy');
    expect(back.getAttribute('href')).toContain('/strategy?id=st-1');
  });
});
