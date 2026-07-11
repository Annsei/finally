/**
 * HistoryCoverageCard.test.tsx — /market historical-data card (D1 §5).
 *
 * Pure helpers:  coverageRows (bare array / wrapped object / garbage → []),
 *                syncToastCounts (success iff bars persisted — auto→sample
 *                fallback rows carry an `error` annotation yet succeed)
 * Rendering:     loading / empty states, per-ticker rows (range, bars, source
 *                label), sync button POST {source:"auto"} + in-flight disabled
 *                spinner + coverage revalidation, success toast counts,
 *                offline fallback toast (all-sample = success), HTTP-failure
 *                toast
 */
import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
}));

import HistoryCoverageCard, {
  HISTORY_COVERAGE_KEY,
  coverageRows,
  syncToastCounts,
} from '@/components/HistoryCoverageCard';
import type { HistoryCoverageRow } from '@/types/market';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const row = (ticker: string, over: Partial<HistoryCoverageRow> = {}): HistoryCoverageRow => ({
  ticker,
  from: '2023-07-03',
  to: '2026-07-01',
  count: 756,
  source: 'sample',
  ...over,
});

const coverageMutate = jest.fn();

function mockData(coverage: unknown) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === HISTORY_COVERAGE_KEY) {
      return { data: coverage, mutate: coverageMutate };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('coverage helpers (D1 §5)', () => {
  it('coverageRows accepts a bare array or a wrapped list and rejects garbage', () => {
    const rows = [row('AAPL')];
    expect(coverageRows(rows)).toEqual(rows);
    expect(coverageRows({ coverage: rows })).toEqual(rows);
    expect(coverageRows({ tickers: rows })).toEqual(rows);
    expect(coverageRows({ results: rows })).toEqual(rows);
    expect(coverageRows({ nope: rows })).toEqual([]);
    expect(coverageRows(null)).toEqual([]);
    expect(coverageRows('x')).toEqual([]);
  });

  it('syncToastCounts splits per-ticker results into ok/failed by persisted bars', () => {
    expect(
      syncToastCounts({
        results: [
          { ticker: 'AAPL', source: 'sample', bars: 756 },
          { ticker: 'NVDA', source: 'sample', bars: 756 },
          { ticker: 'ZZZZ', error: 'unknown ticker' },
        ],
        total_bars: 1512,
      })
    ).toEqual({ ok: 2, failed: 1 });
    expect(syncToastCounts({ results: [] })).toEqual({ ok: 0, failed: 0 });
    expect(syncToastCounts(undefined)).toEqual({ ok: 0, failed: 0 });
  });

  it('syncToastCounts treats auto→sample fallback rows (bars persisted + error annotation) as successes', () => {
    // Backend D1 §2: auto mode falls back to sample on real-source failure and
    // annotates the row with the real source's error — bars still persisted.
    expect(
      syncToastCounts({
        results: [
          { ticker: 'AAPL', source: 'sample', bars: 756, error: 'yfinance: HTTP 502' },
          { ticker: 'NVDA', source: 'sample', bars: 756, error: 'yfinance: timed out' },
          { ticker: 'ZZZZ', source: 'sample', bars: 0, error: 'yfinance: no data; sample: unknown ticker' },
        ],
        total_bars: 1512,
      })
    ).toEqual({ ok: 2, failed: 1 });
  });
});

describe('HistoryCoverageCard (D1 §5)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  it('shows the loading copy while coverage is in flight, then the empty state', () => {
    mockData(undefined);
    const { unmount } = render(<HistoryCoverageCard />);
    expect(screen.getByTestId('history-coverage').textContent).toContain(
      'Loading data coverage…'
    );
    unmount();

    mockData({ coverage: [] });
    render(<HistoryCoverageCard />);
    expect(screen.getByTestId('history-coverage').textContent).toContain(
      'No historical daily bars yet'
    );
  });

  it('renders one row per ticker with range, bar count, and source label', () => {
    mockData({
      coverage: [row('AAPL'), row('600519', { source: 'akshare', count: 730 })],
    });
    render(<HistoryCoverageCard />);

    const aapl = screen.getByTestId('history-coverage-row-AAPL');
    expect(aapl.textContent).toContain('AAPL');
    expect(aapl.textContent).toContain('2023-07-03 → 2026-07-01');
    expect(aapl.textContent).toContain('756');
    expect(aapl.textContent).toContain('Sample');

    const mt = screen.getByTestId('history-coverage-row-600519');
    expect(mt.textContent).toContain('AKShare');
    expect(mt.textContent).toContain('730');
  });

  it('sync POSTs {source:"auto"}, disables the button with a spinner, then revalidates', async () => {
    mockData({ coverage: [] });
    let resolveFetch: (value: unknown) => void = () => {};
    (global.fetch as jest.Mock).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      })
    );
    render(<HistoryCoverageCard />);

    const button = screen.getByTestId('history-sync-button') as HTMLButtonElement;
    expect(button.textContent).toContain('Sync Data');
    await act(async () => {
      fireEvent.click(button);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/market/history/sync',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ source: 'auto' }),
      })
    );
    // in flight: disabled + spinner + syncing copy
    expect(button.disabled).toBe(true);
    expect(screen.getByTestId('history-sync-spinner')).toBeInTheDocument();
    expect(button.textContent).toContain('Syncing…');

    await act(async () => {
      resolveFetch({
        ok: true,
        json: async () => ({ results: [{ ticker: 'AAPL', source: 'sample', bars: 756 }] }),
      });
    });
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(screen.queryByTestId('history-sync-spinner')).toBeNull();
    expect(coverageMutate).toHaveBeenCalled();
  });

  it('the result toast reports success and failure counts', async () => {
    mockData({ coverage: [] });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        results: [
          { ticker: 'AAPL', source: 'sample', bars: 756 },
          { ticker: 'NVDA', source: 'sample', bars: 756 },
          { ticker: 'ZZZZ', error: 'unknown ticker' },
        ],
        total_bars: 1512,
      }),
    });
    render(<HistoryCoverageCard />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('history-sync-button'));
    });

    expect(screen.getByTestId('history-sync-toast').textContent).toBe(
      'Sync complete: 2 succeeded · 1 failed'
    );
  });

  it('an offline auto→sample fallback sync reads as success, not failure (D1 §2/§5)', async () => {
    // Contract's flagship 断网 scenario: every ticker falls back to sample
    // (bars persisted, real source's error annotated) — the toast must count
    // them as successes and keep the green success border.
    mockData({ coverage: [] });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        results: [
          { ticker: 'AAPL', source: 'sample', bars: 756, error: 'yfinance: HTTP 502' },
          { ticker: 'NVDA', source: 'sample', bars: 756, error: 'yfinance: timed out' },
        ],
        total_bars: 1512,
      }),
    });
    render(<HistoryCoverageCard />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('history-sync-button'));
    });

    const toast = screen.getByTestId('history-sync-toast');
    expect(toast.textContent).toBe('Sync complete: 2 succeeded · 0 failed');
    expect(toast.style.border).toContain('rgb(34, 197, 94)'); // #22c55e success
    expect(toast.style.border).not.toContain('rgb(239, 68, 68)'); // not #ef4444
  });

  it('an HTTP failure surfaces the server error in the toast', async () => {
    mockData({ coverage: [] });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 429,
      json: async () => ({ error: 'Sync throttled — try again in a few seconds' }),
    });
    render(<HistoryCoverageCard />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('history-sync-button'));
    });

    expect(screen.getByTestId('history-sync-toast').textContent).toBe(
      'Sync throttled — try again in a few seconds'
    );
    // the button re-arms after a failure
    expect((screen.getByTestId('history-sync-button') as HTMLButtonElement).disabled).toBe(false);
  });
});
