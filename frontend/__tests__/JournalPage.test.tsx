/**
 * JournalPage.test.tsx — /journal review page (P1 §6).
 *
 * Pure helper: groupTradesByDay — local-day grouping, per-day realized P&L
 *              totals + counts, newest day first, invalid timestamps skipped.
 * Rendering:   day sections (journal-day-YYYY-MM-DD) with colored totals,
 *              ticker filter, review archive newest-first with the review
 *              kind border, run-review POST + revalidate, error state,
 *              empty states.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import useSWR from 'swr';
import type { TradeRecord } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
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

import JournalPage, { groupTradesByDay, localDayOf } from '@/pages/journal';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const REVIEWS_KEY = '/api/chat/?kind=review&limit=100';
const TRADES_KEY = '/api/portfolio/trades?limit=500';

// Timezone-naive ISO strings parse as LOCAL time, so the expected local day
// is exactly the date part — deterministic in any TZ the suite runs in.
const trade = (over: Partial<TradeRecord>): TradeRecord => ({
  id: 'tr-x',
  ticker: 'AAPL',
  side: 'buy',
  quantity: 5,
  price: 100,
  executed_at: '2026-07-07T10:00:00',
  commission: 0,
  realized_pnl: null,
  ...over,
});

function mockData(byKey: Record<string, unknown>, mutators: Record<string, jest.Mock> = {}) {
  mockUseSWR.mockImplementation(((key: string) => ({
    data: byKey[key],
    mutate: mutators[key] ?? jest.fn(),
  })) as never);
}

describe('groupTradesByDay (P1 §6)', () => {
  it('groups by local day with counts and realized totals, newest day first', () => {
    const groups = groupTradesByDay([
      trade({ id: 'a', executed_at: '2026-07-07T15:00:00', side: 'sell', realized_pnl: 30 }),
      trade({ id: 'b', executed_at: '2026-07-07T09:00:00', realized_pnl: null }),
      trade({ id: 'c', executed_at: '2026-07-06T12:00:00', side: 'sell', realized_pnl: -12.5 }),
    ]);
    expect(groups.map((g) => g.day)).toEqual(['2026-07-07', '2026-07-06']);
    expect(groups[0].count).toBe(2);
    expect(groups[0].realized).toBeCloseTo(30, 10); // buys contribute 0
    expect(groups[0].trades.map((t) => t.id)).toEqual(['a', 'b']);
    expect(groups[1].count).toBe(1);
    expect(groups[1].realized).toBeCloseTo(-12.5, 10);
  });

  it('skips unparseable timestamps and handles the empty list', () => {
    expect(groupTradesByDay([])).toEqual([]);
    const groups = groupTradesByDay([
      trade({ id: 'bad', executed_at: 'not-a-date' }),
      trade({ id: 'ok', executed_at: '2026-07-05T08:00:00' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].trades.map((t) => t.id)).toEqual(['ok']);
    expect(localDayOf('not-a-date')).toBeNull();
    expect(localDayOf('2026-07-05T08:00:00')).toBe('2026-07-05');
  });
});

describe('JournalPage (P1 §6)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockData({});
  });

  afterEach(() => {
    // restore any fetch stub installed by individual tests
    delete (global as Record<string, unknown>).fetch;
  });

  it('renders day sections with counts and direction-colored realized totals', () => {
    mockData({
      [TRADES_KEY]: {
        trades: [
          trade({ id: 'a', executed_at: '2026-07-07T15:00:00', side: 'sell', realized_pnl: 30 }),
          trade({ id: 'b', ticker: 'NVDA', executed_at: '2026-07-06T12:00:00', side: 'sell', realized_pnl: -12.5 }),
        ],
      },
    });
    render(<JournalPage />);

    expect(screen.getByTestId('journal-days')).toBeTruthy();
    expect(screen.getByTestId('journal-day-2026-07-07')).toBeTruthy();
    expect(screen.getByTestId('journal-day-2026-07-06')).toBeTruthy();

    // journal-realized-${day} — prefix deliberately distinct from the
    // journal-day-${day} section testid so `[data-testid^="journal-day-"]`
    // selectors match day sections only.
    const up = screen.getByTestId('journal-realized-2026-07-07');
    expect(up.textContent).toContain('+$30.00');
    expect(up.className).toContain('text-terminal-up');

    const down = screen.getByTestId('journal-realized-2026-07-06');
    expect(down.textContent).toContain('-$12.50');
    expect(down.className).toContain('text-terminal-down');

    // rows carry SymbolLinks for their tickers
    expect(screen.getByTestId('symbol-link-AAPL')).toBeTruthy();
    expect(screen.getByTestId('symbol-link-NVDA')).toBeTruthy();
  });

  it('filters trades client-side by ticker text', () => {
    mockData({
      [TRADES_KEY]: {
        trades: [
          trade({ id: 'a', ticker: 'AAPL', executed_at: '2026-07-07T15:00:00' }),
          trade({ id: 'b', ticker: 'NVDA', executed_at: '2026-07-07T14:00:00' }),
        ],
      },
    });
    render(<JournalPage />);
    expect(screen.getByTestId('journal-trade-a')).toBeTruthy();
    expect(screen.getByTestId('journal-trade-b')).toBeTruthy();

    fireEvent.change(screen.getByTestId('journal-filter'), { target: { value: 'nv' } });
    expect(screen.queryByTestId('journal-trade-a')).toBeNull();
    expect(screen.getByTestId('journal-trade-b')).toBeTruthy();

    // no match → the i18n empty state
    fireEvent.change(screen.getByTestId('journal-filter'), { target: { value: 'ZZZ' } });
    expect(screen.getByText('No trades yet.')).toBeTruthy();
  });

  it('renders the review archive newest first with the review kind border', () => {
    mockData({
      [REVIEWS_KEY]: {
        // API returns most-recent N in ascending order → page shows reversed
        messages: [
          { role: 'assistant', content: 'older review', kind: 'review', actions: null, created_at: '2026-07-06T20:00:00' },
          { role: 'assistant', content: 'newest review', kind: 'review', actions: null, created_at: '2026-07-07T20:00:00' },
        ],
      },
    });
    render(<JournalPage />);
    const first = screen.getByTestId('journal-review-0');
    expect(first.textContent).toContain('newest review');
    expect(screen.getByTestId('journal-review-1').textContent).toContain('older review');
    // KIND_BORDER.review accent (#ecad0a — jsdom normalizes to rgb) on the left border
    expect(first.getAttribute('style')).toContain('rgb(236, 173, 10)');
  });

  it('run-review POSTs /api/chat/review and revalidates the archive', async () => {
    const mutateReviews = jest.fn().mockResolvedValue(undefined);
    mockData({ [REVIEWS_KEY]: { messages: [] } }, { [REVIEWS_KEY]: mutateReviews });
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    (global as Record<string, unknown>).fetch = fetchMock;

    render(<JournalPage />);
    fireEvent.click(screen.getByTestId('journal-run-review'));

    await waitFor(() => expect(mutateReviews).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith('/api/chat/review', { method: 'POST' });
  });

  it('surfaces a run-review failure inline', async () => {
    mockData({ [REVIEWS_KEY]: { messages: [] } });
    (global as Record<string, unknown>).fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ error: 'LLM unavailable' }),
    });

    render(<JournalPage />);
    fireEvent.click(screen.getByTestId('journal-run-review'));

    await waitFor(() =>
      expect(screen.getByTestId('journal-review-error').textContent).toContain('LLM unavailable')
    );
  });

  it('shows the i18n empty states for reviews and trades', () => {
    mockData({ [REVIEWS_KEY]: { messages: [] }, [TRADES_KEY]: { trades: [] } });
    render(<JournalPage />);
    expect(
      screen.getByText("No reviews yet. Run one to archive today's takeaways.")
    ).toBeTruthy();
    expect(screen.getByText('No trades yet.')).toBeTruthy();
  });
});
