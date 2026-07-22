/**
 * SymbolPage.test.tsx — /symbol?c=CODE detail page (P1 §5).
 *
 * Pure helper: amplitudePct — (high−low)/prev_close×100 with prev_close>0
 *              guard and null-safety.
 * Rendering:   symbol-empty on first-frame hydration (router.query.c
 *              undefined), uppercase normalization, MainChart/TradeBar reuse,
 *              day stats from the live store with /api/market/quotes
 *              fallback, cn limit price rows, my position (found + empty),
 *              per-ticker fills, AI-analyze one-shot chat handoff.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import { useUiStore } from '@/stores/uiStore';
import type { PriceUpdate } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn(),
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
  TRADE_REVALIDATE_KEYS: [
    '/api/portfolio/',
    '/api/portfolio/trades',
    '/api/portfolio/orders?status=open',
    '/api/rules',
    '/api/watchlist/',
  ],
}));

jest.mock('@/components/MainChart', () => ({
  __esModule: true,
  default: ({ ticker }: { ticker: string }) => (
    <div data-testid="main-chart" data-ticker={ticker} />
  ),
}));

jest.mock('@/components/TradeBar', () => ({
  __esModule: true,
  default: ({
    selectedTicker,
    onTradeComplete,
  }: {
    selectedTicker: string | null;
    onTradeComplete?: () => void;
  }) => (
    <div data-testid="trade-bar" data-ticker={selectedTicker ?? ''}>
      <button data-testid="stub-trade-complete" onClick={() => onTradeComplete?.()} />
    </div>
  ),
}));

import { useRouter } from 'next/compat/router';
import { useSWRConfig } from 'swr';
import SymbolPage, { amplitudePct } from '@/pages/symbol';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;
const mockUseSWRConfig = useSWRConfig as jest.MockedFunction<typeof useSWRConfig>;

const update = (over: Partial<PriceUpdate> = {}): PriceUpdate => ({
  ticker: 'NVDA',
  price: 105,
  previous_price: 104,
  timestamp: 1,
  change: 1,
  change_percent: 1,
  direction: 'up',
  prev_close: 100,
  day_change: 5,
  day_change_percent: 5,
  day_high: 110,
  day_low: 100,
  bid: 104.9,
  ask: 105.1,
  volume: 4321,
  ...over,
});

function mockData(byKey: Record<string, unknown>) {
  mockUseSWR.mockImplementation(((key: string) =>
    key in byKey
      ? { data: byKey[key], mutate: jest.fn() }
      : { data: undefined, mutate: jest.fn() }) as never);
}

describe('amplitudePct (P1 §5)', () => {
  it('computes (high − low) / prev_close × 100', () => {
    expect(amplitudePct(110, 100, 100)).toBeCloseTo(10, 10);
    expect(amplitudePct(101.5, 99.25, 90)).toBeCloseTo(2.5, 10);
  });

  it('guards prev_close ≤ 0 and missing/non-finite inputs with null', () => {
    expect(amplitudePct(110, 100, 0)).toBeNull();
    expect(amplitudePct(110, 100, -5)).toBeNull();
    expect(amplitudePct(undefined, 100, 100)).toBeNull();
    expect(amplitudePct(110, null, 100)).toBeNull();
    expect(amplitudePct(110, 100, undefined)).toBeNull();
    expect(amplitudePct(NaN, 100, 100)).toBeNull();
  });
});

describe('SymbolPage (P1 §5)', () => {
  const globalMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    useUiStore.setState({
      portfolioTab: 'positions',
      backtestPrefill: null,
      chatOpen: true,
      chatDraft: '',
      pendingChatMessage: null,
    });
    mockUseSWRConfig.mockReturnValue({ mutate: globalMutate } as never);
    mockData({});
  });

  it('renders the symbol-empty placeholder while router.query.c is undefined', () => {
    // Static export hydration: first frame has query {} (P1 §1); jest's bare
    // mount goes further — no RouterContext at all (compat router → null).
    mockUseRouter.mockReturnValue(null);
    render(<SymbolPage />);
    expect(screen.getByTestId('symbol-empty')).toBeTruthy();
    expect(screen.queryByTestId('symbol-title')).toBeNull();

    // Router mounted but query not ready yet → still the empty state.
    mockUseRouter.mockReturnValue({ query: {} } as never);
    render(<SymbolPage />);
    expect(screen.getAllByTestId('symbol-empty').length).toBeGreaterThan(0);
  });

  it('uppercases the code and mounts MainChart + TradeBar with it', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'nvda' } } as never);
    render(<SymbolPage />);
    expect(screen.getByTestId('symbol-title').textContent).toBe('NVDA');
    expect(screen.getByTestId('main-chart').getAttribute('data-ticker')).toBe('NVDA');
    expect(screen.getByTestId('trade-bar').getAttribute('data-ticker')).toBe('NVDA');
    expect(screen.queryByTestId('symbol-empty')).toBeNull();
  });

  it('renders live day stats incl. the amplitude computation', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    usePriceStore.setState({ prices: { NVDA: update() } });
    render(<SymbolPage />);

    const stats = screen.getByTestId('symbol-stats');
    expect(stats.textContent).toContain('$100.00'); // prev close
    expect(stats.textContent).toContain('$110.00'); // high
    expect(stats.textContent).toContain('$104.90'); // bid
    expect(stats.textContent).toContain('$105.10'); // ask
    // (110 − 100) / 100 × 100 = 10.00%
    expect(screen.getByTestId('symbol-amplitude').textContent).toBe('10.00%');
  });

  it('falls back to the /api/market/quotes snapshot before the stream arrives', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    mockData({
      '/api/market/quotes': {
        quotes: [{ ...update({ price: 99.5, prev_close: 98 }), sector: 'tech' }],
      },
    });
    render(<SymbolPage />);
    expect(screen.getByTestId('symbol-price').textContent).toBe('$99.50');
    expect(screen.getByTestId('symbol-stats').textContent).toContain('$98.00');
  });

  it('cn: shows 涨停/跌停 price rows only when the quote carries limits', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    usePriceStore.setState({
      prices: { NVDA: update({ limit_up: 115.5, limit_down: 94.5 }) },
    });
    render(<SymbolPage />);
    expect(screen.getByTestId('symbol-limit-up').textContent).toBe('$115.50');
    expect(screen.getByTestId('symbol-limit-up').className).toContain('text-terminal-up');
    expect(screen.getByTestId('symbol-limit-down').textContent).toBe('$94.50');
    expect(screen.getByTestId('symbol-limit-down').className).toContain('text-terminal-down');
  });

  it('US quotes without limits render no limit rows', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    usePriceStore.setState({ prices: { NVDA: update() } });
    render(<SymbolPage />);
    expect(screen.queryByTestId('symbol-limit-up')).toBeNull();
    expect(screen.queryByTestId('symbol-limit-down')).toBeNull();
  });

  it('shows my position when the portfolio holds the code, empty state otherwise', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    mockData({
      '/api/portfolio/': {
        cash: 5000,
        total_value: 11000,
        positions: [
          {
            ticker: 'NVDA',
            quantity: 10,
            avg_cost: 95,
            current_price: 105,
            unrealized_pnl: 100,
            pnl_pct: 10.53,
          },
        ],
      },
    });
    render(<SymbolPage />);
    const pos = screen.getByTestId('symbol-position');
    expect(pos.textContent).toContain('10'); // qty
    expect(pos.textContent).toContain('$95.00'); // avg cost
    expect(pos.textContent).toContain('+$100.00'); // unrealized P&L
    expect(pos.textContent).toContain('+10.53%');

    // Without a matching position → i18n empty state
    mockData({
      '/api/portfolio/': { cash: 5000, total_value: 5000, positions: [] },
    });
    render(<SymbolPage />);
    expect(screen.getAllByText('No position in NVDA.').length).toBeGreaterThan(0);
  });

  it('lists my fills for this ticker from /api/portfolio/trades?ticker=…', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    mockData({
      '/api/portfolio/trades?ticker=NVDA&limit=100': {
        trades: [
          {
            id: 'tr-1',
            ticker: 'NVDA',
            side: 'buy',
            quantity: 5,
            price: 100,
            executed_at: '2026-07-07T10:00:00',
            commission: 0,
            realized_pnl: null,
          },
          {
            id: 'tr-2',
            ticker: 'NVDA',
            side: 'sell',
            quantity: 2,
            price: 110,
            executed_at: '2026-07-07T11:00:00',
            commission: 0,
            realized_pnl: 20,
          },
        ],
      },
    });
    render(<SymbolPage />);
    expect(screen.getByTestId('symbol-trade-tr-1').textContent).toContain('Buy');
    const sell = screen.getByTestId('symbol-trade-tr-2');
    expect(sell.textContent).toContain('Sell');
    expect(sell.textContent).toContain('+$20.00');
  });

  it('AI analyze hands the prompt to the global chat as a one-shot message', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    useUiStore.setState({ chatOpen: false, pendingChatMessage: null });
    render(<SymbolPage />);

    fireEvent.click(screen.getByTestId('symbol-ai-analyze'));
    const state = useUiStore.getState();
    expect(state.pendingChatMessage).toBe(
      "Analyze NVDA for me: given my current position and today's price action, should I adjust?"
    );
    expect(state.chatOpen).toBe(true);
  });

  it('onTradeComplete revalidates the desk key set plus this ticker blotter', () => {
    mockUseRouter.mockReturnValue({ query: { c: 'NVDA' } } as never);
    render(<SymbolPage />);
    fireEvent.click(screen.getByTestId('stub-trade-complete'));
    const keys = globalMutate.mock.calls.map((c) => c[0]);
    expect(keys).toEqual(
      expect.arrayContaining([
        '/api/portfolio/',
        '/api/portfolio/trades',
        '/api/portfolio/orders?status=open',
        '/api/rules',
        '/api/watchlist/',
        '/api/portfolio/trades?ticker=NVDA&limit=100',
      ])
    );
  });
});
