/**
 * AppShellStrategies.test.tsx — P2 §8 strategies revalidation.
 *
 * AppShell.test.tsx pins refreshAfterTrade to exactly the five desk keys via
 * useSWRConfig().mutate, so '/api/strategies' revalidates through SWR's
 * module-level mutate (same default cache — no custom SWRConfig provider).
 * This suite mocks BOTH entry points and proves an AI action revalidates the
 * strategies key alongside the pinned desk set.
 */
import React from 'react';
import { render, act, screen } from '@testing-library/react';
import { useUiStore } from '@/stores/uiStore';
import { usePriceStore } from '@/stores/priceStore';

const namedMutate = jest.fn();

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined, mutate: jest.fn() }),
  useSWRConfig: jest.fn(),
  mutate: (...args: unknown[]) => namedMutate(...args),
}));

jest.mock('@/components/ChatPanel', () => ({
  __esModule: true,
  default: ({ onNewTrade }: { onNewTrade?: () => void }) => (
    <div data-testid="chat-panel">
      <button data-testid="stub-trade" onClick={() => onNewTrade?.()} />
    </div>
  ),
}));

import { useSWRConfig } from 'swr';
const mockUseSWRConfig = useSWRConfig as jest.MockedFunction<typeof useSWRConfig>;

import AppShell, { TRADE_REVALIDATE_KEYS, STRATEGIES_REVALIDATE_KEY } from '@/components/AppShell';

describe('AppShell strategies revalidation (P2)', () => {
  const globalMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useUiStore.setState({ chatOpen: true, chatDraft: '', pendingChatMessage: null });
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    mockUseSWRConfig.mockReturnValue({ mutate: globalMutate } as never);
  });

  it('exports the strategies key without disturbing the pinned desk key set', () => {
    expect(STRATEGIES_REVALIDATE_KEY).toBe('/api/strategies');
    expect(TRADE_REVALIDATE_KEYS).toEqual([
      '/api/portfolio/',
      '/api/portfolio/trades',
      '/api/portfolio/orders?status=open',
      '/api/rules',
      '/api/watchlist/',
    ]);
  });

  it('onNewTrade revalidates /api/strategies via the module-level mutate', () => {
    render(
      <AppShell>
        <div />
      </AppShell>
    );

    act(() => {
      screen.getByTestId('stub-trade').click();
    });

    // Desk set still flows through the provider mutate (5 calls, unchanged)…
    expect(globalMutate).toHaveBeenCalledTimes(5);
    // …and the strategies key through the module-level entry point.
    expect(namedMutate).toHaveBeenCalledTimes(1);
    expect(namedMutate).toHaveBeenCalledWith('/api/strategies');
  });
});
