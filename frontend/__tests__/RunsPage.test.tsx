/**
 * RunsPage.test.tsx — /runs run library page (P2 §8).
 *
 * Pure helper:   filterRuns (ticker substring + strategy id, both optional)
 * Rendering:     loading / empty states, rows (time, SymbolLink, strategy
 *                name link, label, direction-coloured return, win rate,
 *                max DD), client-side filters, row click → /run?id=…, and the
 *                two-click delete confirm (DELETE + revalidate)
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';
import type { BacktestRunListItem, BacktestStats, Strategy } from '@/types/market';

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

import { useRouter } from 'next/compat/router';
import RunsPage, { filterRuns, RUNS_KEY } from '@/pages/runs';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

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

const run = (id: string, over: Partial<BacktestRunListItem> = {}): BacktestRunListItem => ({
  id,
  strategy_id: null,
  label: null,
  created_at: '2026-07-07T10:00:00Z',
  ticker: 'AAPL',
  days: 30,
  runs: 1,
  seed: 42,
  stats: stats(),
  ...over,
});

const strategyStub = (id: string, name: string): Strategy =>
  ({ id, name, ticker: 'NVDA', status: 'draft' }) as Strategy;

const listMutate = jest.fn();

function mockData(opts: { runs?: BacktestRunListItem[]; strategies?: Strategy[] }) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === RUNS_KEY) {
      return { data: opts.runs ? { runs: opts.runs } : undefined, mutate: listMutate };
    }
    if (key === '/api/strategies?status=all') {
      return { data: { strategies: opts.strategies ?? [] }, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('filterRuns (P2 §8)', () => {
  const runs = [
    run('r1', { ticker: 'AAPL' }),
    run('r2', { ticker: 'NVDA', strategy_id: 'st-1' }),
    run('r3', { ticker: 'NVDA', strategy_id: 'st-2' }),
  ];

  it('is the identity with empty filters', () => {
    expect(filterRuns(runs, '', '')).toHaveLength(3);
  });

  it('matches ticker as a case-insensitive substring', () => {
    expect(filterRuns(runs, 'nvd', '').map((r) => r.id)).toEqual(['r2', 'r3']);
    expect(filterRuns(runs, '  AAPL ', '').map((r) => r.id)).toEqual(['r1']);
    expect(filterRuns(runs, 'ZZZ', '')).toHaveLength(0);
  });

  it('matches strategy id exactly and composes with the ticker filter', () => {
    expect(filterRuns(runs, '', 'st-1').map((r) => r.id)).toEqual(['r2']);
    expect(filterRuns(runs, 'NVDA', 'st-2').map((r) => r.id)).toEqual(['r3']);
    expect(filterRuns(runs, 'AAPL', 'st-1')).toHaveLength(0);
  });
});

describe('RunsPage (P2 §8)', () => {
  const push = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    mockUseRouter.mockReturnValue({ push } as never);
    mockData({ runs: [] });
  });

  it('shows the loading state before data and the i18n empty state after', () => {
    mockData({});
    const { unmount } = render(<RunsPage />);
    expect(screen.getByText('Loading runs…')).toBeInTheDocument();
    unmount();

    mockData({ runs: [] });
    render(<RunsPage />);
    expect(screen.getByText(/No saved backtests yet/)).toBeInTheDocument();
  });

  it('renders rows: time, SymbolLink, strategy name link, label, coloured stats', () => {
    mockData({
      runs: [
        run('r1', {
          strategy_id: 'st-1',
          label: 'baseline',
          stats: stats({ total_return_pct: -2.5, win_rate: null }),
        }),
      ],
      strategies: [strategyStub('st-1', 'Dip Buyer')],
    });
    render(<RunsPage />);

    const row = screen.getByTestId('run-row-r1');
    expect(screen.getByTestId('symbol-link-AAPL')).toBeInTheDocument();
    expect(screen.getByTestId('run-strategy-link-r1').textContent).toBe('Dip Buyer');
    expect(row.textContent).toContain('baseline');
    expect(row.textContent).toContain('-2.50%');
    expect(row.querySelector('.text-terminal-down')).toBeTruthy();
    expect(row.textContent).toContain('−3.87%'); // max DD
    expect(row.textContent).toContain('—'); // null win rate
  });

  it('unattributed runs show an em-dash instead of a strategy link', () => {
    mockData({ runs: [run('r1')] });
    render(<RunsPage />);
    expect(screen.queryByTestId('run-strategy-link-r1')).toBeNull();
    expect(screen.getByTestId('run-row-r1').textContent).toContain('—');
  });

  it('ticker filter and strategy dropdown narrow the table client-side', () => {
    mockData({
      runs: [
        run('r1', { ticker: 'AAPL' }),
        run('r2', { ticker: 'NVDA', strategy_id: 'st-1' }),
      ],
      strategies: [strategyStub('st-1', 'Dip Buyer')],
    });
    render(<RunsPage />);
    expect(screen.getByTestId('run-row-r1')).toBeInTheDocument();
    expect(screen.getByTestId('run-row-r2')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('runs-filter-ticker'), { target: { value: 'nvda' } });
    expect(screen.queryByTestId('run-row-r1')).toBeNull();
    expect(screen.getByTestId('run-row-r2')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('runs-filter-ticker'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('runs-filter-strategy'), { target: { value: 'st-1' } });
    expect(screen.queryByTestId('run-row-r1')).toBeNull();
    expect(screen.getByTestId('run-row-r2')).toBeInTheDocument();
  });

  it('row click routes to /run?id=…', () => {
    mockData({ runs: [run('r1')] });
    render(<RunsPage />);
    fireEvent.click(screen.getByTestId('run-row-r1'));
    expect(push).toHaveBeenCalledWith({ pathname: '/run', query: { id: 'r1' } });
  });

  it('row Enter key routes to /run?id=…', () => {
    mockData({ runs: [run('r1')] });
    render(<RunsPage />);
    const row = screen.getByTestId('run-row-r1');
    row.focus();
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(push).toHaveBeenCalledWith({ pathname: '/run', query: { id: 'r1' } });
  });

  it('delete is a two-click confirm: arm, then DELETE + revalidate', async () => {
    mockData({ runs: [run('r1')] });
    render(<RunsPage />);

    const del = screen.getByTestId('run-delete-r1');
    expect(del.textContent).toBe('Delete');
    fireEvent.click(del);
    // First click only arms — nothing leaves the client, row still routes not.
    expect(global.fetch).not.toHaveBeenCalled();
    expect(del.textContent).toBe('Confirm delete?');
    expect(push).not.toHaveBeenCalled(); // stopPropagation kept the row inert

    await act(async () => {
      fireEvent.click(del);
    });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/backtest/runs/r1',
      expect.objectContaining({ method: 'DELETE' })
    );
    expect(listMutate).toHaveBeenCalled();
  });

  it('a delete failure surfaces the error inline', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ error: 'run not found' }),
    });
    mockData({ runs: [run('r1')] });
    render(<RunsPage />);
    fireEvent.click(screen.getByTestId('run-delete-r1'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('run-delete-r1'));
    });
    expect(screen.getByTestId('runs-delete-error').textContent).toBe('run not found');
  });
});
