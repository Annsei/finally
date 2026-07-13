/**
 * MarketPageReplay.test.tsx — /market replay banner integration (D3 §3).
 *
 * The banner is a PURE ADDITION to the page: outside replay mode nothing
 * renders (the pre-D3 sections are untouched); in replay mode the banner
 * mounts above the grid.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';

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
// content renders in isolation (same recipe as MarketPage.test.tsx).
jest.mock('@/components/AppShell', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

import { useRouter } from 'next/compat/router';
import MarketPage from '@/pages/market';
import { REPLAY_STATUS_KEY } from '@/components/ReplayStatus';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

function mockKeys(byKey: Record<string, unknown>) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key in byKey) return { data: byKey[key], mutate: jest.fn() };
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('MarketPage × replay banner (D3 §3)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    mockUseRouter.mockReturnValue({ push: jest.fn() } as never);
  });

  it('default deployment → no replay banner, existing sections intact', () => {
    mockKeys({ '/api/market/quotes': { quotes: [] } });
    render(<MarketPage />);

    expect(screen.queryByTestId('replay-banner')).toBeNull();
    // pre-D3 page furniture still renders
    expect(screen.getByText('All Symbols')).toBeTruthy();
    expect(screen.getByTestId('history-coverage')).toBeTruthy();
  });

  it('active replay → banner mounts with window, progress, and day copy', () => {
    mockKeys({
      '/api/market/quotes': { quotes: [] },
      [REPLAY_STATUS_KEY]: {
        active: true,
        from: '2020-03-02',
        to: '2020-03-27',
        current_date: '2020-03-16',
        day_index: 2, // 0-based (backend shape) — renders as day 3/20
        total_days: 20,
        seconds_per_day: 120,
        loop: true,
        finished: false,
        source_hint: 'sample',
      },
    });
    render(<MarketPage />);

    const banner = screen.getByTestId('replay-banner');
    expect(banner).toBeTruthy();
    expect(screen.getByTestId('replay-banner-window').textContent).toBe(
      '2020-03-02 → 2020-03-27'
    );
    expect(screen.getByTestId('replay-banner-day').textContent).toBe('Day 3/20 · 2020-03-16');
    expect(screen.getByTestId('replay-banner-progress').getAttribute('data-pct')).toBe('15');
    // additive: the grid section still renders alongside the banner
    expect(screen.getByText('All Symbols')).toBeTruthy();
  });
});
