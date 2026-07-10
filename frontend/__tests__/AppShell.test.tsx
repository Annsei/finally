/**
 * AppShell.test.tsx — P1 §2 shared chrome for /market /symbol /journal /arena.
 *
 * Test 1: chrome is complete — Header, NewsTicker, main children, ChatPanel
 *         dock, StatusBar
 * Test 2: narrow screens scroll naturally; md+ uses the viewport-lock pattern
 * Test 3: chat dock width follows uiStore.chatOpen (w-80 ↔ w-8) and
 *         onToggle writes back to the store
 * Test 4: onNewTrade revalidates the same key set as the desk's
 *         refreshAfterTrade + the watchlist
 */
import React from 'react';
import { render, act, screen } from '@testing-library/react';
import { useUiStore } from '@/stores/uiStore';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({ data: undefined, mutate: jest.fn() }),
  useSWRConfig: jest.fn(),
}));

// Stub ChatPanel — AppShell must mount the SAME component with the standard
// props contract {open, onToggle, onNewTrade}; the stub exposes hooks to fire
// the callbacks.
jest.mock('@/components/ChatPanel', () => ({
  __esModule: true,
  default: ({ open, onToggle, onNewTrade }: { open: boolean; onToggle: () => void; onNewTrade?: () => void }) => (
    <div data-testid="chat-panel" data-open={String(open)}>
      <button data-testid="stub-toggle" onClick={onToggle} />
      <button data-testid="stub-trade" onClick={() => onNewTrade?.()} />
    </div>
  ),
}));

import { useSWRConfig } from 'swr';
const mockUseSWRConfig = useSWRConfig as jest.MockedFunction<typeof useSWRConfig>;

import AppShell from '@/components/AppShell';

describe('AppShell (P1)', () => {
  const globalMutate = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    useUiStore.setState({
      portfolioTab: 'positions',
      backtestPrefill: null,
      chatOpen: true,
      chatDraft: '',
      pendingChatMessage: null,
    });
    usePriceStore.setState({ connectionStatus: 'disconnected', prices: {} });
    mockUseSWRConfig.mockReturnValue({ mutate: globalMutate } as never);
  });

  it('Test 1: renders the full chrome around the page content', () => {
    render(
      <AppShell>
        <div data-testid="page-content">hello</div>
      </AppShell>
    );

    // Header (brand nav) + NewsTicker + StatusBar + ChatPanel + children
    expect(screen.getByTestId('nav-desk')).toBeTruthy();
    expect(screen.getByTestId('connection-status')).toBeTruthy();
    expect(screen.getByTestId('news-ticker')).toBeTruthy();
    expect(screen.getByTestId('status-clock')).toBeTruthy();
    expect(screen.getByTestId('status-feed-latency')).toBeTruthy();
    expect(screen.getByTestId('chat-panel')).toBeTruthy();
    expect(screen.getByTestId('page-content')).toBeTruthy();
    // children live inside the scrollable content region — a <div>, not a
    // nested <main> (_app.tsx owns the single top-level <main> landmark)
    const content = screen.getByTestId('page-content').parentElement as HTMLElement;
    expect(content.tagName).toBe('DIV');
    expect(content.className).toContain('overflow-auto');
    expect(screen.getByTestId('page-content').closest('main')).toBeNull();
  });

  it('Test 2: viewport contract scrolls on narrow screens and locks panels at md+', () => {
    const { container } = render(
      <AppShell>
        <div />
      </AppShell>
    );
    const root = container.firstChild as HTMLElement;
    for (const cls of ['min-h-screen', 'md:h-screen', 'md:overflow-hidden', 'flex', 'flex-col', 'bg-terminal-bg', 'text-terminal-text']) {
      expect(root.className).toContain(cls);
    }
    expect(root.classList.contains('h-screen')).toBe(false);
    expect(root.classList.contains('overflow-hidden')).toBe(false);

    const layout = screen.getByTestId('app-shell-layout');
    expect(layout.className).toContain('flex-col');
    expect(layout.className).toContain('md:flex-row');
    expect(layout.className).toContain('p-2');
    expect(layout.className).toContain('lg:p-4');
  });

  it('Test 3: chat dock reads uiStore.chatOpen (w-80/w-8) and onToggle writes it back', () => {
    render(
      <AppShell>
        <div />
      </AppShell>
    );

    const dock = screen.getByTestId('chat-panel').parentElement as HTMLElement;
    expect(dock.tagName).toBe('ASIDE');
    expect(dock.getAttribute('aria-label')).toBe('FinAlly AI chat');
    expect(screen.getByTestId('chat-panel').getAttribute('data-open')).toBe('true');
    expect(dock.className).toContain('w-80');
    expect(dock.className).toContain('fixed');
    expect(dock.className).toContain('md:w-96');
    expect(dock.className).toContain('xl:static');
    expect(dock.className).toContain('xl:w-72');
    expect(dock.className).toContain('2xl:w-80');

    act(() => {
      screen.getByTestId('stub-toggle').click();
    });
    expect(useUiStore.getState().chatOpen).toBe(false);
    expect(screen.getByTestId('chat-panel').getAttribute('data-open')).toBe('false');
    expect(dock.className).toContain('w-8');
    expect(dock.className).toContain('w-10');
    expect(dock.className).toContain('xl:w-8');
    expect(dock.className).not.toContain('w-80');
  });

  it('Test 4: onNewTrade revalidates the desk key set plus the watchlist', () => {
    render(
      <AppShell>
        <div />
      </AppShell>
    );

    act(() => {
      screen.getByTestId('stub-trade').click();
    });

    const keys = globalMutate.mock.calls.map((c) => c[0]);
    expect(keys).toEqual(
      expect.arrayContaining([
        '/api/portfolio/',
        '/api/portfolio/trades',
        '/api/portfolio/orders?status=open',
        '/api/rules',
        '/api/watchlist/',
      ])
    );
    expect(globalMutate).toHaveBeenCalledTimes(5);
  });
});
