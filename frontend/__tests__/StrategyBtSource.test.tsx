/**
 * StrategyBtSource.test.tsx — strategy-detail backtest launcher source switch
 * (D1 §5, `strategy-bt-source`). The pre-existing launcher behaviour is pinned
 * by StrategyDetailPage.test.tsx (untouched): this file covers the additive
 * history mode — runs input disabled + pinned to 1, days relabelled as trading
 * days (20..750), and `source: "history"` in the POST /api/backtest/runs body.
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';
import type { Strategy } from '@/types/market';

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
import StrategyPage from '@/pages/strategy';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

const strategyFixture: Strategy = {
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
  runs_count: 1,
  realized_pnl: 0,
};

function mockData() {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/strategies/st-1') {
      return { data: { strategy: strategyFixture }, mutate: jest.fn() };
    }
    if (key === '/api/backtest/runs?strategy_id=st-1') {
      return { data: { runs: [] }, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('strategy detail backtest source switch (D1 §5)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    mockUseRouter.mockReturnValue({ query: { id: 'st-1' } } as never);
    mockData();
  });

  it('renders the strategy-bt-source switch defaulting to simulated', () => {
    render(<StrategyPage />);
    expect(screen.getByTestId('strategy-bt-source')).toBeInTheDocument();
    expect(
      screen.getByTestId('strategy-bt-source-synthetic').getAttribute('aria-pressed')
    ).toBe('true');
    expect(screen.getByText('Days')).toBeInTheDocument();
    expect((screen.getByLabelText('Runs') as HTMLInputElement).disabled).toBe(false);
  });

  it('history mode disables the runs input and relabels days as trading days', () => {
    render(<StrategyPage />);
    fireEvent.click(screen.getByTestId('strategy-bt-source-history'));

    expect((screen.getByLabelText('Runs') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('Trading days')).toBeInTheDocument();
    const daysInput = screen.getByLabelText('Trading days') as HTMLInputElement;
    expect(daysInput.min).toBe('20');
    expect(daysInput.max).toBe('750');
  });

  it('history run POSTs {strategy_id, days, runs: 1, source: "history"}', async () => {
    render(<StrategyPage />);
    fireEvent.change(screen.getByLabelText('Runs'), { target: { value: '10' } });
    fireEvent.click(screen.getByTestId('strategy-bt-source-history'));
    fireEvent.change(screen.getByLabelText('Trading days'), { target: { value: '120' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-run-backtest'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/backtest/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ strategy_id: 'st-1', days: 120, runs: 1, source: 'history' }),
      })
    );
  });

  it('switching back to simulated restores the legacy POST body', async () => {
    render(<StrategyPage />);
    fireEvent.click(screen.getByTestId('strategy-bt-source-history'));
    fireEvent.click(screen.getByTestId('strategy-bt-source-synthetic'));
    fireEvent.change(screen.getByLabelText('Runs'), { target: { value: '10' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('strategy-run-backtest'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/backtest/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ strategy_id: 'st-1', days: 30, runs: 10 }),
      })
    );
  });
});
