/**
 * BacktestSavePanel.test.tsx — Backtest-tab "Save to Runs" (P2 §8).
 *
 * The save affordance exists only once a result is rendered. Saving POSTs
 * /api/backtest/runs with the FULL legacy field set — including the seed the
 * server echoed in result.config, so the persisted run re-runs byte-identically
 * (contract §5) — plus the optional label. Success shows a toast linking to
 * /runs; failure surfaces the server error. BacktestPanel.test.tsx pins the
 * pre-existing panel behaviour and stays untouched.
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
  trades: [],
  runs_summary: null,
};

const runBacktest = async () => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => fixture });
  await act(async () => {
    fireEvent.click(screen.getByTestId('backtest-run'));
  });
  await waitFor(() => expect(screen.getByTestId('backtest-stats')).toBeInTheDocument());
};

describe('BacktestPanel save-to-Runs (P2 §8)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    useUiStore.setState({ portfolioTab: 'backtest', backtestPrefill: null });
  });

  it('no save affordance before a result exists', () => {
    render(<BacktestPanel />);
    expect(screen.queryByTestId('backtest-save')).toBeNull();
    expect(screen.queryByTestId('backtest-save-label')).toBeNull();
  });

  it('saving POSTs the legacy field set in full — seed included — plus the label', async () => {
    render(<BacktestPanel />);
    await runBacktest();

    expect(screen.getByTestId('backtest-save')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('backtest-save-label'), {
      target: { value: 'baseline sweep' },
    });

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ run: { id: 'run-1' } }),
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('backtest-save'));
    });

    // Call 0 was the /api/backtest run; call 1 is the persistence POST.
    const [url, init] = (global.fetch as jest.Mock).mock.calls[1];
    expect(url).toBe('/api/backtest/runs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      ticker: 'AAPL',
      trigger_type: 'day_change_pct_below',
      threshold: -2,
      quantity: 5,
      take_profit_pct: 5,
      stop_loss_pct: 3,
      days: 30,
      runs: 1,
      seed: 42,
      label: 'baseline sweep',
    });

    // Success toast links to the run library; the button disarms.
    const toast = screen.getByTestId('backtest-save-toast');
    expect(toast.textContent).toContain('Saved');
    expect(toast.getAttribute('href')).toBe('/runs');
    expect((screen.getByTestId('backtest-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('an empty label is omitted from the POST body', async () => {
    render(<BacktestPanel />);
    await runBacktest();

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ run: { id: 'run-2' } }),
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('backtest-save'));
    });

    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[1][1].body);
    expect(body).not.toHaveProperty('label');
    expect(body.seed).toBe(42);
  });

  it('a save failure surfaces the server error and keeps the button armed', async () => {
    render(<BacktestPanel />);
    await runBacktest();

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'label too long' }),
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('backtest-save'));
    });

    expect(screen.getByTestId('backtest-save-error').textContent).toBe('label too long');
    expect(screen.queryByTestId('backtest-save-toast')).toBeNull();
    expect((screen.getByTestId('backtest-save') as HTMLButtonElement).disabled).toBe(false);
  });

  it('a fresh backtest run re-arms the save affordance', async () => {
    render(<BacktestPanel />);
    await runBacktest();

    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ run: { id: 'run-3' } }),
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('backtest-save'));
    });
    expect(screen.getByTestId('backtest-save-toast')).toBeInTheDocument();

    // Running the backtest again clears the saved state and toast.
    await runBacktest();
    expect(screen.queryByTestId('backtest-save-toast')).toBeNull();
    expect((screen.getByTestId('backtest-save') as HTMLButtonElement).disabled).toBe(false);
  });
});
