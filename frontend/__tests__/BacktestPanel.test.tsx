/**
 * BacktestPanel tests (M5 — strategy backtester):
 * Test 1: form renders with defaults; nothing fetched on mount
 * Test 2: Run POSTs /api/backtest and renders stats, chart series, and trades
 * Test 3: API 400 error surfaces in backtest-error, no stats rendered
 * Test 4: client-side validation blocks the fetch (bad quantity)
 * Test 5: uiStore prefill populates the form and is consumed (cleared)
 * Test 6: runs > 1 renders the Monte Carlo runs-summary strip
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

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

import { createChart, BaselineSeries, LineSeries } from 'lightweight-charts';
import BacktestPanel from '@/components/BacktestPanel';
import { useUiStore } from '@/stores/uiStore';
import type { BacktestResponse } from '@/types/market';
import type { MarketProfile } from '@/lib/marketProfile';

const CN_PROFILE: MarketProfile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  t_plus: 1,
  up_is_red: true,
  seed_cash: 100000,
  midday_break: true,
  names: { '600519': '贵州茅台' },
  price_limit_pct: { '600519': 10 },
};

const fixture: BacktestResponse = {
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
    anchor_price: 190.0,
  },
  stats: {
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
  },
  equity_curve: [
    { time: 1751000000, value: 10000 },
    { time: 1751000060, value: 10431.22 },
  ],
  baseline_curve: [
    { time: 1751000000, value: 10000 },
    { time: 1751000060, value: 10602 },
  ],
  trades: [
    { time: 1751000000, side: 'buy', price: 186.1, quantity: 5, reason: 'trigger', pnl: null },
    { time: 1751000060, side: 'sell', price: 195.4, quantity: 5, reason: 'take_profit', pnl: 46.5 },
  ],
  runs_summary: null,
};

const runBacktest = () => fireEvent.click(screen.getByTestId('backtest-run'));

describe('BacktestPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    useUiStore.setState({ portfolioTab: 'backtest', backtestPrefill: null });
  });

  it('Test 1: renders the config form with defaults and fetches nothing on mount', () => {
    render(<BacktestPanel />);

    expect((screen.getByLabelText('Backtest ticker') as HTMLInputElement).value).toBe('AAPL');
    expect((screen.getByTestId('backtest-trigger') as HTMLSelectElement).value).toBe(
      'day_change_pct_below'
    );
    expect((screen.getByLabelText('Backtest quantity') as HTMLInputElement).value).toBe('5');
    expect((screen.getByLabelText('Days') as HTMLInputElement).value).toBe('30');
    expect(screen.getByTestId('backtest-run')).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
    expect(screen.queryByTestId('backtest-stats')).not.toBeInTheDocument();
  });

  it('Test 2: Run POSTs the config and renders stats, both chart series, and the trades table', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => fixture,
    });

    render(<BacktestPanel />);
    await act(async () => {
      runBacktest();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/backtest',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          ticker: 'AAPL',
          trigger_type: 'day_change_pct_below',
          threshold: -2,
          quantity: 5,
          take_profit_pct: 5,
          stop_loss_pct: 3,
          days: 30,
          runs: 1,
        }),
      })
    );

    await waitFor(() => expect(screen.getByTestId('backtest-stats')).toBeInTheDocument());
    expect(screen.getByTestId('backtest-return').textContent).toBe('+4.31%');
    expect(screen.getByText('+6.02%')).toBeInTheDocument(); // buy & hold card

    // Chart mounts with the strategy BaselineSeries (base $10k) + dashed buy&hold LineSeries
    const mc = jest.mocked(createChart);
    expect(mc).toHaveBeenCalledTimes(1);
    const chart = mc.mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 10000 } })
    );
    expect(chart.addSeries).toHaveBeenCalledWith(LineSeries, expect.objectContaining({ lineStyle: 2 }));
    const equitySeries = (chart.addSeries.mock.results[0] as jest.MockResult<{ setData: jest.Mock }>)
      .value;
    expect(equitySeries.setData).toHaveBeenCalledWith([
      { time: 1751000000, value: 10000 },
      { time: 1751000060, value: 10431.22 },
    ]);

    // Trades blotter
    const trades = screen.getByTestId('backtest-trades');
    expect(trades.textContent).toContain('take profit');
    expect(trades.textContent).toContain('+$46.50');
    // Single run → no Monte Carlo strip
    expect(screen.queryByTestId('backtest-runs-summary')).not.toBeInTheDocument();
  });

  it('Test 3: a 400 from the API surfaces its error message and renders no stats', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Ticker not found' }),
    });

    render(<BacktestPanel />);
    await act(async () => {
      runBacktest();
    });

    await waitFor(() =>
      expect(screen.getByTestId('backtest-error').textContent).toBe('Ticker not found')
    );
    expect(screen.queryByTestId('backtest-stats')).not.toBeInTheDocument();
  });

  it('Test 4: invalid quantity is rejected client-side without a network call', () => {
    render(<BacktestPanel />);

    fireEvent.change(screen.getByLabelText('Backtest quantity'), { target: { value: '0' } });
    runBacktest();

    expect(screen.getByTestId('backtest-error').textContent).toContain('Quantity');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('Test 5: a uiStore prefill populates the form and is consumed', async () => {
    useUiStore.setState({
      backtestPrefill: {
        ticker: 'NVDA',
        trigger_type: 'day_change_pct_below',
        threshold: -3,
        quantity: 5,
      },
    });

    render(<BacktestPanel />);

    await waitFor(() =>
      expect((screen.getByLabelText('Backtest ticker') as HTMLInputElement).value).toBe('NVDA')
    );
    expect((screen.getByLabelText('Threshold') as HTMLInputElement).value).toBe('-3');
    expect(useUiStore.getState().backtestPrefill).toBeNull();
  });

  it('Test 6: runs > 1 renders the Monte Carlo distribution strip', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...fixture,
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

    render(<BacktestPanel />);
    fireEvent.click(screen.getByTestId('backtest-runs-30'));
    await act(async () => {
      runBacktest();
    });

    await waitFor(() => expect(screen.getByTestId('backtest-runs-summary')).toBeInTheDocument());
    const strip = screen.getByTestId('backtest-runs-summary');
    expect(strip.textContent).toContain('30 runs');
    expect(strip.textContent).toContain('+3.10%');
    expect(strip.textContent).toContain('-6.20%');
    // The POST body carries runs: 30
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body as string);
    expect(body.runs).toBe(30);
  });

  it('CN defaults to a configured ticker and whole-lot quantity', () => {
    render(<BacktestPanel profile={CN_PROFILE} />);
    expect((screen.getByLabelText('回测代码') as HTMLInputElement).value).toBe('600519');
    expect((screen.getByLabelText('回测数量') as HTMLInputElement).value).toBe('100');

    fireEvent.change(screen.getByLabelText('回测数量'), { target: { value: '50' } });
    runBacktest();
    expect(screen.getByTestId('backtest-error').textContent).toContain('100');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('CN equity chart anchors at the profile seed cash', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => fixture });
    render(<BacktestPanel profile={CN_PROFILE} />);
    await act(async () => runBacktest());
    await waitFor(() => expect(screen.getByTestId('backtest-stats')).toBeInTheDocument());

    const chart = jest.mocked(createChart).mock.results[0].value as { addSeries: jest.Mock };
    expect(chart.addSeries).toHaveBeenCalledWith(
      BaselineSeries,
      expect.objectContaining({ baseValue: { type: 'price', price: 100000 } })
    );
  });
});
