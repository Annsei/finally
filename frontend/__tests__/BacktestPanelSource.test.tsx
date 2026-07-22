/**
 * BacktestPanelSource.test.tsx — Backtest-tab data-source switch (D1 §5).
 *
 * The pre-existing panel behaviour is pinned by BacktestPanel.test.tsx /
 * BacktestSavePanel.test.tsx (untouched). This file covers the additive
 * history mode: switch → runs pinned to 1 + disabled, days relabelled as
 * trading days with the 20..750 window, `source: "history"` in the POST,
 * the result-block source badge + date_range, and the history save
 * passthrough.
 */
import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

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

import BacktestPanel from '@/components/BacktestPanel';
import { useUiStore } from '@/stores/uiStore';
import type { BacktestResponse } from '@/types/market';

const fixture = (over: Partial<BacktestResponse['config']> = {}): BacktestResponse => ({
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
    ...over,
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
  trades: [],
  runs_summary: null,
});

const HISTORY_CONFIG = {
  seed: null,
  source: 'sample',
  date_range: { from: '2026-01-02', to: '2026-07-01' },
} as const;

const runBacktest = () => fireEvent.click(screen.getByTestId('backtest-run'));
const switchToHistory = () => fireEvent.click(screen.getByTestId('backtest-source-history'));

describe('BacktestPanel data source (D1 §5)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    useUiStore.setState({ portfolioTab: 'backtest', backtestPrefill: null });
  });

  it('defaults to the simulated segment; days label and helper are the legacy copy', () => {
    render(<BacktestPanel />);
    expect(screen.getByTestId('backtest-source')).toBeInTheDocument();
    expect(screen.getByTestId('backtest-source-synthetic').getAttribute('aria-pressed')).toBe(
      'true'
    );
    expect(screen.getByText('Days')).toBeInTheDocument();
    expect(screen.queryByText('Trading days')).toBeNull();
    expect(screen.getByText(/Simulated history \(GBM/)).toBeInTheDocument();
  });

  it('history mode disables the runs selector, pins runs to 1, and relabels days', () => {
    render(<BacktestPanel />);
    fireEvent.click(screen.getByTestId('backtest-runs-30'));
    switchToHistory();

    expect(screen.getByTestId('backtest-source-history').getAttribute('aria-pressed')).toBe(
      'true'
    );
    for (const r of [1, 10, 30]) {
      expect((screen.getByTestId(`backtest-runs-${r}`) as HTMLButtonElement).disabled).toBe(true);
    }
    // switching re-pins the selection to the deterministic single run
    expect(screen.getByTestId('backtest-runs-1').className).toContain('border-terminal-blue');
    expect(screen.getByText('Trading days')).toBeInTheDocument();
    const daysInput = screen.getByLabelText('Days') as HTMLInputElement;
    expect(daysInput.min).toBe('20');
    expect(daysInput.max).toBe('750');
    // history helper copy replaces the GBM copy
    expect(screen.getByText(/next day's open/)).toBeInTheDocument();
  });

  it('history submit carries source:"history" and runs:1', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => fixture(HISTORY_CONFIG),
    });
    render(<BacktestPanel />);
    fireEvent.click(screen.getByTestId('backtest-runs-10'));
    switchToHistory();
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
          source: 'history',
        }),
      })
    );
  });

  it('switching back to simulated restores the legacy payload (no source field)', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => fixture() });
    render(<BacktestPanel />);
    switchToHistory();
    fireEvent.click(screen.getByTestId('backtest-source-synthetic'));
    await act(async () => {
      runBacktest();
    });
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body as string);
    expect(body).not.toHaveProperty('source');
    expect(body.runs).toBe(1);
  });

  it('validates the trading-day window client-side: 20..750', () => {
    render(<BacktestPanel />);
    switchToHistory();

    fireEvent.change(screen.getByLabelText('Days'), { target: { value: '10' } });
    runBacktest();
    expect(screen.getByTestId('backtest-error').textContent).toBe(
      'Trading days must be an integer between 20 and 750.'
    );
    expect(global.fetch).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Days'), { target: { value: '751' } });
    runBacktest();
    expect(screen.getByTestId('backtest-error').textContent).toBe(
      'Trading days must be an integer between 20 and 750.'
    );
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('a history days value beyond the synthetic cap (e.g. 200) is accepted', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => fixture({ ...HISTORY_CONFIG, days: 200 }),
    });
    render(<BacktestPanel />);
    switchToHistory();
    fireEvent.change(screen.getByLabelText('Days'), { target: { value: '200' } });
    await act(async () => {
      runBacktest();
    });
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body as string);
    expect(body.days).toBe(200);
    expect(body.source).toBe('history');
  });

  it('the result block renders backtest-source-badge with the echoed date_range', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => fixture(HISTORY_CONFIG),
    });
    render(<BacktestPanel />);
    switchToHistory();
    await act(async () => {
      runBacktest();
    });
    await waitFor(() => expect(screen.getByTestId('backtest-stats')).toBeInTheDocument());

    const badge = screen.getByTestId('backtest-source-badge');
    expect(badge.getAttribute('data-source')).toBe('sample');
    expect(badge.textContent).toContain('Sample');
    expect(badge.textContent).toContain('2026-01-02 → 2026-07-01');
  });

  it('a synthetic result renders the badge as Simulated without a range', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => fixture() });
    render(<BacktestPanel />);
    await act(async () => {
      runBacktest();
    });
    await waitFor(() => expect(screen.getByTestId('backtest-stats')).toBeInTheDocument());
    const badge = screen.getByTestId('backtest-source-badge');
    expect(badge.getAttribute('data-source')).toBe('synthetic');
    expect(badge.textContent).toBe('Simulated');
  });

  it('saving a history result passes source:"history" through to the Run Library', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => fixture(HISTORY_CONFIG),
    });
    render(<BacktestPanel />);
    switchToHistory();
    await act(async () => {
      runBacktest();
    });
    await waitFor(() => expect(screen.getByTestId('backtest-save')).toBeInTheDocument());

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ run: { id: 'run-h1' } }),
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('backtest-save'));
    });

    const [url, init] = (global.fetch as jest.Mock).mock.calls[1];
    expect(url).toBe('/api/backtest/runs');
    const body = JSON.parse(init.body as string);
    expect(body.source).toBe('history');
    expect(body.seed).toBeNull(); // history runs echo seed: null (D1 §3)
    expect(body.runs).toBe(1);
  });
});
