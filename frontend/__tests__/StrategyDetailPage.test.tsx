/**
 * StrategyDetailPage.test.tsx — /strategy?id=X detail page (P2 §8).
 *
 * Pure helpers:  conditionText (en/zh, money via formatMoney), exitsText,
 *                sizingText (formatShares lots on cn), compareRows shaping
 * Rendering:     hydration strategy-empty, 404 → not-found, config summary,
 *                soft-gate deploy (two clicks when runs_count === 0, direct
 *                otherwise), pause / archive-confirm PATCHes, performance
 *                stats + 0-baseline chart, run-backtest POST, run rows +
 *                two-run compare table
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';
import type {
  BacktestRunListItem,
  BacktestStats,
  Strategy,
  StrategyPerformanceResponse,
} from '@/types/market';

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
import StrategyPage, {
  conditionText,
  exitsText,
  sizingText,
  compareRows,
} from '@/pages/strategy';
import { makeT } from '@/lib/i18n';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

const tEn = makeT('en');
const tZh = makeT('zh');
const usd = { currency_symbol: '$', locale: 'en-US' };
const cny = { currency_symbol: '¥', locale: 'zh-CN' };

const stats = (over: Partial<BacktestStats> = {}): BacktestStats => ({
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
  ...over,
});

const runItem = (id: string, over: Partial<BacktestRunListItem> = {}): BacktestRunListItem => ({
  id,
  strategy_id: 'st-1',
  label: null,
  created_at: '2026-07-07T10:00:00Z',
  ticker: 'NVDA',
  days: 30,
  runs: 1,
  seed: 42,
  stats: stats(),
  ...over,
});

const strategyFixture = (over: Partial<Strategy> = {}): Strategy => ({
  id: 'st-1',
  name: 'Dip Buyer',
  ticker: 'NVDA',
  status: 'draft',
  entry: { all: [{ field: 'day_change_pct', op: 'below', value: -3 }] },
  exits: { take_profit_pct: 4, stop_loss_pct: 3 },
  sizing: { mode: 'cash_pct', pct: 20 },
  template: 'dip_buyer',
  created_at: '2026-07-07T00:00:00Z',
  deployed_at: null,
  open_qty: 0,
  open_price: null,
  opened_at: null,
  entered_count: 0,
  exited_count: 0,
  last_fired_at: null,
  runs_count: 0,
  realized_pnl: 0,
  ...over,
});

const perfFixture: StrategyPerformanceResponse = {
  stats: {
    realized_pnl: 55.25,
    round_trips: 2,
    win_rate: 0.5,
    profit_factor: 1.8,
    max_drawdown_pct: 1.2,
    fires: 3,
  },
  equity_curve: [
    { time: 1_751_000_000, value: 0 },
    { time: 1_751_000_060, value: 55.25 },
  ],
  trades: [],
};

const strategyMutate = jest.fn();
const runsMutate = jest.fn();

function mockData(opts: {
  strategy?: Strategy;
  strategyError?: boolean;
  performance?: StrategyPerformanceResponse;
  runs?: BacktestRunListItem[];
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/strategies/st-1') {
      if (opts.strategyError) return { data: undefined, error: new Error('404'), mutate: strategyMutate };
      return { data: opts.strategy ? { strategy: opts.strategy } : undefined, mutate: strategyMutate };
    }
    if (key === '/api/strategies/st-1/performance') {
      return { data: opts.performance, mutate: jest.fn() };
    }
    if (key === '/api/backtest/runs?strategy_id=st-1') {
      return { data: { runs: opts.runs ?? [] }, mutate: runsMutate };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('strategy config text helpers (P2 §8 conditionText)', () => {
  it('renders price conditions through formatMoney (en $ / zh ¥)', () => {
    const group = { all: [{ field: 'price', op: 'below' as const, value: 1200 }] };
    expect(conditionText(group, tEn, usd)).toBe('all of: price ≤ $1,200.00');
    const zh = conditionText(group, tZh, cny);
    expect(zh).toBe('全部满足: 价格 ≤ ¥1,200.00');
  });

  it('renders every whitelisted field as a human sentence', () => {
    expect(
      conditionText({ all: [{ field: 'day_change_pct', op: 'below', value: -3 }] }, tEn, usd)
    ).toBe('all of: day change ≤ -3%');
    expect(
      conditionText(
        { all: [{ field: 'ma_cross', op: 'above', params: { fast: 5, slow: 20 } }] },
        tEn,
        usd
      )
    ).toBe('all of: SMA(5)/SMA(20) golden cross');
    expect(
      conditionText(
        { all: [{ field: 'ema_cross', op: 'below', params: { fast: 5, slow: 20 } }] },
        tEn,
        usd
      )
    ).toBe('all of: EMA(5)/EMA(20) death cross');
    expect(
      conditionText(
        { all: [{ field: 'rsi', op: 'below', value: 30, params: { period: 14 } }] },
        tEn,
        usd
      )
    ).toBe('all of: RSI(14) ≤ 30');
    expect(
      conditionText({ all: [{ field: 'window_high', op: 'above', params: { minutes: 60 } }] }, tEn, usd)
    ).toBe('all of: breaks the 60-minute high');
    expect(
      conditionText({ all: [{ field: 'window_low', op: 'below', params: { minutes: 90 } }] }, tEn, usd)
    ).toBe('all of: breaks the 90-minute low');
    expect(
      conditionText(
        {
          all: [
            { field: 'pullback_from_high_pct', op: 'above', value: 2, params: { minutes: 60 } },
          ],
        },
        tEn,
        usd
      )
    ).toBe('all of: pulls back ≥ 2% from the 60-minute high');
    expect(
      conditionText(
        { all: [{ field: 'ma', op: 'above', value: 0, params: { period: 30 } }] },
        tEn,
        usd
      )
    ).toBe('all of: price ≥ SMA(30) by 0%');
  });

  it('joins multiple conditions and honours the any-mode joiner', () => {
    const group = {
      any: [
        { field: 'day_change_pct', op: 'above' as const, value: 0.5 },
        { field: 'window_high', op: 'above' as const, params: { minutes: 60 } },
      ],
    };
    expect(conditionText(group, tEn, usd)).toBe(
      'any of: day change ≥ 0.5% · breaks the 60-minute high'
    );
    expect(conditionText(null, tEn, usd)).toBe('—');
    expect(conditionText({ all: [] }, tEn, usd)).toBe('—');
  });

  it('exitsText lists non-empty exits and falls back to the none placeholder', () => {
    expect(
      exitsText(
        { take_profit_pct: 4, stop_loss_pct: 3, trailing_stop_pct: 2.5, max_holding_days: 10 },
        tEn
      )
    ).toBe('TP 4% · SL 3% · Trailing stop 2.5% · Max hold 10d');
    expect(exitsText({}, tEn)).toBe('No exits');
    expect(exitsText({ take_profit_pct: null, stop_loss_pct: null }, tEn)).toBe('No exits');
  });

  it('sizingText renders fixed qty via formatShares (lots on cn) and cash pct', () => {
    expect(sizingText({ mode: 'fixed_qty', qty: 5 }, tEn, { lot_size: 1 })).toBe('Fixed qty 5');
    expect(sizingText({ mode: 'cash_pct', pct: 20 }, tEn, { lot_size: 1 })).toBe('20% of cash');
    expect(sizingText({ mode: 'fixed_qty', qty: 200 }, tZh, { lot_size: 100 })).toBe(
      '固定数量 2手'
    );
    expect(sizingText({ mode: 'cash_pct', pct: 15 }, tZh, { lot_size: 100 })).toBe('现金的 15%');
  });
});

describe('compareRows shaping (P2 §8 runs-compare)', () => {
  it('pairs both runs stats with direction classes', () => {
    const a = runItem('r1', { stats: stats({ total_return_pct: 4.31 }) });
    const b = runItem('r2', { stats: stats({ total_return_pct: -2.1, win_rate: null, profit_factor: null }), days: 60 });
    const rows = compareRows(a, b, tEn);

    const ret = rows.find((r) => r.label === 'Return')!;
    expect(ret.a).toBe('+4.31%');
    expect(ret.b).toBe('-2.10%');
    expect(ret.aClass).toBe('text-terminal-up');
    expect(ret.bClass).toBe('text-terminal-down');

    expect(rows.find((r) => r.label === 'Win rate')).toEqual(
      expect.objectContaining({ a: '67%', b: '—' })
    );
    expect(rows.find((r) => r.label === 'Profit factor')).toEqual(
      expect.objectContaining({ a: '2.33', b: '—' })
    );
    expect(rows.find((r) => r.label === 'Max DD')!.a).toBe('−3.87%');
    expect(rows.find((r) => r.label === 'Days')).toEqual(
      expect.objectContaining({ a: '30', b: '60' })
    );
  });
});

describe('StrategyPage (P2 §8)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    mockUseRouter.mockReturnValue({ query: { id: 'st-1' } } as never);
    mockData({ strategy: strategyFixture(), performance: perfFixture });
  });

  it('renders the strategy-empty hydration placeholder until the query resolves', () => {
    mockUseRouter.mockReturnValue({ query: {} } as never);
    render(<StrategyPage />);
    expect(screen.getByTestId('strategy-empty')).toBeInTheDocument();
    expect(screen.getByTestId('strategy-empty').textContent).toBe('No strategy selected.');
  });

  it('shows the not-found message when the fetch errors', () => {
    mockData({ strategyError: true });
    render(<StrategyPage />);
    expect(screen.getByText('Strategy not found.')).toBeInTheDocument();
  });

  it('renders the human-readable config summary (entry / exits / sizing)', () => {
    render(<StrategyPage />);
    const config = screen.getByTestId('strategy-config');
    expect(config.textContent).toContain('all of: day change ≤ -3%');
    expect(config.textContent).toContain('TP 4% · SL 3%');
    expect(config.textContent).toContain('20% of cash');
    expect(config.textContent).toContain('No open position.');
  });

  it('soft gate: deploying a never-backtested draft takes two clicks', async () => {
    render(<StrategyPage />);
    const deploy = screen.getByTestId('strategy-deploy');
    expect(deploy.textContent).toBe('Deploy');

    // First click only arms the confirmation — no PATCH leaves the client.
    fireEvent.click(deploy);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('strategy-deploy-warning').textContent).toContain(
      'never been backtested'
    );
    expect(deploy.textContent).toBe('Confirm deploy?');

    // Second click confirms → PATCH live.
    await act(async () => {
      fireEvent.click(deploy);
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/st-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'live' }) })
    );
    expect(strategyMutate).toHaveBeenCalled();
  });

  it('deploy is a single click once the strategy has saved runs', async () => {
    mockData({
      strategy: strategyFixture({ runs_count: 2 }),
      performance: perfFixture,
      runs: [runItem('r1')],
    });
    render(<StrategyPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-deploy'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/st-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'live' }) })
    );
    expect(screen.queryByTestId('strategy-deploy-warning')).toBeNull();
  });

  it('live: pause PATCHes paused and the pause-semantics hint is visible', async () => {
    mockData({ strategy: strategyFixture({ status: 'live' }), performance: perfFixture });
    render(<StrategyPage />);
    expect(screen.getByText(/fully frozen/)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-pause'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/st-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'paused' }) })
    );
  });

  it('archive needs a confirming second click, then PATCHes archived', async () => {
    render(<StrategyPage />);
    const archive = screen.getByTestId('strategy-archive');
    fireEvent.click(archive);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(archive.textContent).toBe('Confirm archive?');
    await act(async () => {
      fireEvent.click(archive);
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/strategies/st-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'archived' }) })
    );
  });

  it('a PATCH failure surfaces the server error inline', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'deploy requires at least one exit' }),
    });
    mockData({ strategy: strategyFixture({ runs_count: 1 }), performance: perfFixture });
    render(<StrategyPage />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-deploy'));
    });
    expect(screen.getByTestId('strategy-action-error').textContent).toBe(
      'deploy requires at least one exit'
    );
  });

  it('performance section shows the stat cards and mounts the 0-baseline chart', () => {
    render(<StrategyPage />);
    const perf = screen.getByTestId('strategy-performance');
    expect(screen.getByTestId('strategy-perf-pnl').textContent).toBe('+$55.25');
    expect(perf.textContent).toContain('50%'); // win rate
    expect(perf.textContent).toContain('1.80'); // profit factor

    // The realized-P&L curve charts against a base value of 0 (P2 §6/§8).
    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 0 } })
    );
  });

  it('run-backtest POSTs /api/backtest/runs {strategy_id, days, runs} and revalidates', async () => {
    render(<StrategyPage />);
    fireEvent.change(screen.getByLabelText('Days'), { target: { value: '20' } });
    fireEvent.change(screen.getByLabelText('Runs'), { target: { value: '10' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-run-backtest'));
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/backtest/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ strategy_id: 'st-1', days: 20, runs: 10 }),
      })
    );
    expect(runsMutate).toHaveBeenCalled();
    expect(strategyMutate).toHaveBeenCalled();
  });

  it('lists the strategy runs and renders the two-run compare table on selection', () => {
    mockData({
      strategy: strategyFixture({ runs_count: 2 }),
      performance: perfFixture,
      runs: [
        runItem('r1', { stats: stats({ total_return_pct: 4.31 }) }),
        runItem('r2', { label: 'tuned', stats: stats({ total_return_pct: -2.1 }) }),
      ],
    });
    render(<StrategyPage />);

    expect(screen.getByTestId('run-row-r1')).toBeInTheDocument();
    expect(screen.getByTestId('run-row-r2').textContent).toContain('tuned');
    expect(screen.queryByTestId('runs-compare')).toBeNull();

    fireEvent.click(screen.getByTestId('run-compare-r1'));
    fireEvent.click(screen.getByTestId('run-compare-r2'));

    const compare = screen.getByTestId('runs-compare');
    expect(compare.textContent).toContain('+4.31%');
    expect(compare.textContent).toContain('-2.10%');
    expect(compare.textContent).toContain('Return');

    // Unchecking one collapses the comparison again.
    fireEvent.click(screen.getByTestId('run-compare-r1'));
    expect(screen.queryByTestId('runs-compare')).toBeNull();
  });
});
