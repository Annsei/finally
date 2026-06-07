/**
 * index.test.tsx — Dashboard integration render tests
 *
 * Test 5 (FE-03): Rendering the index page produces a root element whose
 *   className contains `bg-terminal-bg` (dark terminal theme applied at page root).
 * Test 6 (FE-09,10,11,12,13,14): All 6 Phase 4 panels mount in the 3-column layout.
 * Test 7 (D-03): When watchlist has tickers and none is selected, first ticker is auto-selected.
 * Test 8 (D-09): Chat panel is open by default (open prop true).
 * Test 9 (D-01): Three distinct layout columns are present.
 */
import React from 'react';
import { render, act } from '@testing-library/react';

jest.mock('@/hooks/usePriceStream', () => ({
  usePriceStream: jest.fn(),
}));

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined, mutate: jest.fn() }),
}));

jest.mock('@/components/SparklineChart', () => ({
  __esModule: true,
  default: () => <div data-testid="sparkline-stub" />,
}));

// Stub all Phase 4 heavy components to avoid canvas/SSE deps in tests
jest.mock('@/components/MainChart', () => ({
  __esModule: true,
  default: ({ ticker }: { ticker: string }) => (
    <div data-testid="main-chart" data-ticker={ticker} />
  ),
}));

jest.mock('@/components/PortfolioHeatmap', () => ({
  __esModule: true,
  default: () => <div data-testid="portfolio-heatmap" />,
}));

jest.mock('@/components/PnLChart', () => ({
  __esModule: true,
  default: () => <div data-testid="pnl-chart" />,
}));

jest.mock('@/components/PositionsTable', () => ({
  __esModule: true,
  default: () => <div data-testid="positions-table" />,
}));

jest.mock('@/components/TradeBar', () => ({
  __esModule: true,
  default: ({ selectedTicker }: { selectedTicker: string | null; onTradeComplete?: () => void }) => (
    <div data-testid="trade-bar" data-ticker={selectedTicker ?? ''} />
  ),
}));

jest.mock('@/components/ChatPanel', () => ({
  __esModule: true,
  default: ({ open, onToggle }: { open: boolean; onToggle: () => void; onNewTrade?: () => void }) => (
    <div data-testid="chat-panel" data-open={String(open)} />
  ),
}));

// Re-import swr so we can configure per test
import useSWR from 'swr';
const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

import Dashboard from '@/pages/index';

describe('Dashboard index page', () => {
  beforeEach(() => {
    mockUseSWR.mockReturnValue({ data: undefined, mutate: jest.fn() } as any);
  });

  it('Test 5 (FE-03): root element className contains bg-terminal-bg', () => {
    const { container } = render(<Dashboard />);
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain('bg-terminal-bg');
  });

  it('Test 6: all 6 Phase 4 panels mount', () => {
    const { getByTestId } = render(<Dashboard />);
    expect(getByTestId('main-chart')).toBeTruthy();
    expect(getByTestId('portfolio-heatmap')).toBeTruthy();
    expect(getByTestId('pnl-chart')).toBeTruthy();
    expect(getByTestId('positions-table')).toBeTruthy();
    expect(getByTestId('trade-bar')).toBeTruthy();
    expect(getByTestId('chat-panel')).toBeTruthy();
  });

  it('Test 7 (D-03): first watchlist ticker is auto-selected when no ticker is selected', () => {
    // Mock watchlist SWR to return ticker data
    mockUseSWR.mockImplementation((key: any) => {
      if (key === '/api/watchlist') {
        return {
          data: { tickers: [{ ticker: 'AAPL' }, { ticker: 'GOOGL' }] },
          mutate: jest.fn(),
        } as any;
      }
      return { data: undefined, mutate: jest.fn() } as any;
    });

    const { getByTestId } = render(<Dashboard />);
    // After useEffect runs, MainChart should receive AAPL (the first ticker)
    const mainChart = getByTestId('main-chart');
    expect(mainChart.getAttribute('data-ticker')).toBe('AAPL');
  });

  it('Test 8 (D-09): chat panel is open by default', () => {
    const { getByTestId } = render(<Dashboard />);
    const chatPanel = getByTestId('chat-panel');
    expect(chatPanel.getAttribute('data-open')).toBe('true');
  });

  it('Test 9 (D-01): three columns are present (watchlist, center, chat)', () => {
    const { container } = render(<Dashboard />);
    // Center column: flex-1 flex flex-col
    const centerCol = container.querySelector('.flex-1.flex.flex-col');
    expect(centerCol).toBeTruthy();
    // Chat column: shrink-0 with w-80 or w-8
    const chatCol = container.querySelector('[class*="shrink-0"]');
    expect(chatCol).toBeTruthy();
  });
});
