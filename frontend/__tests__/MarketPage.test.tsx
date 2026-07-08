/**
 * MarketPage.test.tsx — /market full-market page (P1 §4).
 *
 * Pure helpers:  sortQuotes (default code asc, day/volume + tie-break),
 *                heatSaturation (linear, clamped at a 3% move)
 * Rendering:     grid rows, default order, header-click sorting, live
 *                priceStore overlay, row click → /symbol?c=…, sector heatmap
 *                tiles (direction class + data-heat), cn name column + limit
 *                badge, event archive list + load-more + empty state
 */
import React from 'react';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import type { MarketQuote } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
}));

jest.mock('next/compat/router', () => ({
  __esModule: true,
  useRouter: jest.fn(),
}));

// AppShell chrome is covered by AppShell.test.tsx — stub it so the page's own
// content renders in isolation.
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

import { useRouter } from 'next/compat/router';
import MarketPage, { sortQuotes, heatSaturation, DEFAULT_SORT } from '@/pages/market';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

const quote = (ticker: string, over: Partial<MarketQuote> = {}): MarketQuote => ({
  ticker,
  price: 100,
  previous_price: 99,
  timestamp: 1,
  change: 1,
  change_percent: 1,
  direction: 'up',
  prev_close: 98,
  day_change: 1.5,
  day_change_percent: 1.5,
  day_high: 101,
  day_low: 97,
  bid: 99.9,
  ask: 100.1,
  volume: 1000,
  sector: 'tech',
  ...over,
});

const ARCHIVE_KEY = '/api/market/events/archive?limit=50';

function mockData(opts: {
  quotes?: MarketQuote[];
  archive?: { events: unknown[]; has_more: boolean };
  profile?: Record<string, unknown>;
}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/market/quotes' && opts.quotes) {
      return { data: { quotes: opts.quotes }, mutate: jest.fn() };
    }
    if (key === ARCHIVE_KEY && opts.archive) {
      return { data: opts.archive, mutate: jest.fn() };
    }
    if (key === '/api/market/profile' && opts.profile) {
      return { data: opts.profile, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

function rowOrder(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-testid^="market-row-"]')].map((el) =>
    (el.getAttribute('data-testid') ?? '').replace('market-row-', '')
  );
}

describe('market page helpers (P1 §4)', () => {
  const quotes = [
    quote('MSFT', { day_change_percent: -2, volume: 500 }),
    quote('AAPL', { day_change_percent: 1.5, volume: 2000 }),
    quote('NVDA', { day_change_percent: 3.2, volume: 500 }),
  ];

  it('sorts by code ascending by default (deterministic)', () => {
    expect(sortQuotes(quotes, DEFAULT_SORT).map((q) => q.ticker)).toEqual([
      'AAPL',
      'MSFT',
      'NVDA',
    ]);
  });

  it('sorts by day change % with direction and by volume with ticker tie-break', () => {
    expect(sortQuotes(quotes, { key: 'day', dir: 'desc' }).map((q) => q.ticker)).toEqual([
      'NVDA',
      'AAPL',
      'MSFT',
    ]);
    expect(sortQuotes(quotes, { key: 'day', dir: 'asc' }).map((q) => q.ticker)).toEqual([
      'MSFT',
      'AAPL',
      'NVDA',
    ]);
    // volume desc: AAPL(2000) first; MSFT/NVDA tie at 500 breaks by ticker asc
    expect(sortQuotes(quotes, { key: 'volume', dir: 'desc' }).map((q) => q.ticker)).toEqual([
      'AAPL',
      'MSFT',
      'NVDA',
    ]);
  });

  it('heatSaturation is linear in |day %| and clamps at a 3% move', () => {
    expect(heatSaturation(0)).toBe(0);
    expect(heatSaturation(1.5)).toBe(50);
    expect(heatSaturation(-1.5)).toBe(50);
    expect(heatSaturation(3)).toBe(100);
    expect(heatSaturation(9)).toBe(100);
    expect(heatSaturation(-12)).toBe(100);
    expect(heatSaturation(undefined)).toBe(0);
    expect(heatSaturation(null)).toBe(0);
  });
});

describe('MarketPage (P1 §4)', () => {
  const push = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    mockUseRouter.mockReturnValue({ push } as never);
    mockData({ quotes: [] });
  });

  it('renders the grid with one row per quote, code ascending by default', () => {
    mockData({
      quotes: [quote('NVDA'), quote('AAPL'), quote('MSFT')],
    });
    const { container } = render(<MarketPage />);
    expect(screen.getByTestId('market-grid')).toBeTruthy();
    expect(rowOrder(container)).toEqual(['AAPL', 'MSFT', 'NVDA']);
  });

  it('header clicks re-sort client-side: day % desc first, click again flips', () => {
    mockData({
      quotes: [
        quote('AAPL', { day_change_percent: 1.5 }),
        quote('MSFT', { day_change_percent: -2 }),
        quote('NVDA', { day_change_percent: 3.2 }),
      ],
    });
    const { container } = render(<MarketPage />);

    fireEvent.click(screen.getByTestId('market-sort-day'));
    expect(rowOrder(container)).toEqual(['NVDA', 'AAPL', 'MSFT']);

    fireEvent.click(screen.getByTestId('market-sort-day'));
    expect(rowOrder(container)).toEqual(['MSFT', 'AAPL', 'NVDA']);

    // back to code asc via the code header
    fireEvent.click(screen.getByTestId('market-sort-code'));
    expect(rowOrder(container)).toEqual(['AAPL', 'MSFT', 'NVDA']);
  });

  it('overlays live priceStore updates over the REST snapshot', () => {
    mockData({ quotes: [quote('AAPL', { price: 100 })] });
    render(<MarketPage />);
    expect(screen.getByTestId('market-row-AAPL').textContent).toContain('100.00');

    act(() => {
      usePriceStore.setState({
        prices: {
          AAPL: {
            ...quote('AAPL'),
            price: 123.45,
            timestamp: 2,
            direction: 'up',
          },
        },
      });
    });
    expect(screen.getByTestId('market-row-AAPL').textContent).toContain('123.45');
  });

  it('renders rows for live-only tickers before the snapshot arrives', () => {
    mockData({}); // /api/market/quotes still loading
    usePriceStore.setState({ prices: { TSLA: quote('TSLA') } });
    render(<MarketPage />);
    expect(screen.getByTestId('market-row-TSLA')).toBeTruthy();
  });

  it('row click navigates to /symbol?c=TICKER', () => {
    mockData({ quotes: [quote('AAPL')] });
    render(<MarketPage />);
    fireEvent.click(screen.getByTestId('market-row-AAPL'));
    expect(push).toHaveBeenCalledWith({ pathname: '/symbol', query: { c: 'AAPL' } });
  });

  it('heatmap tiles carry direction classes, clamped data-heat, and sector groups', () => {
    mockData({
      quotes: [
        quote('AAPL', { day_change_percent: 1.5, sector: 'tech' }),
        quote('JPM', { day_change_percent: -9, sector: 'financials' }),
        quote('GS', { day_change_percent: 0, sector: 'financials' }),
      ],
    });
    render(<MarketPage />);
    const heatmap = screen.getByTestId('market-heatmap');
    // sector group labels render inside the heatmap (scoped — the grid's
    // sector chips show the same text)
    expect(within(heatmap).getByText('tech')).toBeTruthy();
    expect(within(heatmap).getByText('financials')).toBeTruthy();

    const up = screen.getByTestId('market-heatmap-tile-AAPL');
    expect(up.className).toContain('text-terminal-up');
    expect(up.getAttribute('data-direction')).toBe('up');
    expect(up.getAttribute('data-heat')).toBe('50');

    const down = screen.getByTestId('market-heatmap-tile-JPM');
    expect(down.className).toContain('text-terminal-down');
    expect(down.getAttribute('data-direction')).toBe('down');
    expect(down.getAttribute('data-heat')).toBe('100'); // |−9| clamps at 3% full scale

    // flat (0%) tiles are neutral — same treatment as the grid rows' 0 case
    const flat = screen.getByTestId('market-heatmap-tile-GS');
    expect(flat.className).toContain('text-terminal-muted');
    expect(flat.className).not.toContain('text-terminal-up');
    expect(flat.getAttribute('data-direction')).toBe('flat');
    expect(flat.getAttribute('data-heat')).toBe('0');

    // tile click also routes to the symbol page
    fireEvent.click(down);
    expect(push).toHaveBeenCalledWith({ pathname: '/symbol', query: { c: 'JPM' } });
  });

  it('cn: shows the profile name column and the 涨停 limit badge', () => {
    mockData({
      quotes: [
        quote('600519', {
          price: 1870,
          limit_up: 1870,
          limit_down: 1530,
          sector: 'consumer',
        }),
      ],
      profile: {
        market: 'cn',
        currency_symbol: '¥',
        locale: 'zh-CN',
        lot_size: 100,
        up_is_red: true,
        names: { '600519': '贵州茅台' },
        price_limit_pct: { '600519': 10 },
      },
    });
    render(<MarketPage />);
    const row = screen.getByTestId('market-row-600519');
    expect(row.textContent).toContain('贵州茅台');
    expect(screen.getByTestId('limit-badge-600519').textContent).toBe('涨停');
  });

  it('event archive renders items (narrative as muted second line) and load-more', () => {
    mockData({
      quotes: [quote('AAPL')],
      archive: {
        events: [
          {
            id: 'ev-1',
            ticker: 'NVDA',
            headline: 'NVDA surges 3.1%',
            narrative: 'Chips rally on AI demand.',
            change_percent: 3.1,
            direction: 'up',
            timestamp: 1_751_800_000,
          },
        ],
        has_more: true,
      },
    });
    render(<MarketPage />);
    expect(screen.getByTestId('market-events')).toBeTruthy();
    const item = screen.getByTestId('market-event-ev-1');
    expect(item.textContent).toContain('NVDA surges 3.1%');
    expect(item.textContent).toContain('Chips rally on AI demand.');
    expect(item.textContent).toContain('+3.10%');
    // the ticker code links to the symbol page
    expect(screen.getByTestId('symbol-link-NVDA')).toBeTruthy();
    // has_more → paging button
    expect(screen.getByTestId('market-events-more')).toBeTruthy();
  });

  it('event archive shows the i18n empty state and hides load-more when exhausted', () => {
    mockData({
      quotes: [quote('AAPL')],
      archive: { events: [], has_more: false },
    });
    render(<MarketPage />);
    expect(screen.getByTestId('market-events').textContent).toContain(
      'No archived market events yet.'
    );
    expect(screen.queryByTestId('market-events-more')).toBeNull();
  });
});
