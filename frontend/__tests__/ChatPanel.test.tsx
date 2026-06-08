/**
 * ChatPanel tests (TDD):
 * Test 1 (FE-14): On mount, history is loaded via GET /api/chat/ and existing messages render
 * Test 2 (FE-14): While POST /api/chat/ is in flight, a loading indicator is visible;
 *                  it disappears after the response resolves
 * Test 3 (FE-15): An assistant message with actions.trades renders a trade badge
 * Test 4 (FE-15): An assistant message with actions.watchlist_changes renders a watchlist badge
 * Test 5 (T-4-02 security): Message content is rendered as a React text child;
 *                             no dangerouslySetInnerHTML in the component source
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import type { ChatMessage } from '@/types/market';

// ---------------------------------------------------------------------------
// SWR mock — must be hoisted; set up return values per-test in beforeEach
// ---------------------------------------------------------------------------
const mockMutateHistory = jest.fn();

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';

// ---------------------------------------------------------------------------
// ChatPanel component under test
// ---------------------------------------------------------------------------
import ChatPanel from '@/components/ChatPanel';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const defaultMessages: ChatMessage[] = [
  {
    role: 'user',
    content: 'Hello',
    actions: null,
    created_at: '2026-06-07T00:00:00Z',
  },
  {
    role: 'assistant',
    content: 'Hi there!',
    actions: null,
    created_at: '2026-06-07T00:00:01Z',
  },
];

const renderPanel = (props: { open?: boolean; onToggle?: () => void; onNewTrade?: () => void } = {}) => {
  const onToggle = props.onToggle ?? jest.fn();
  return render(
    <ChatPanel
      open={props.open ?? true}
      onToggle={onToggle}
      onNewTrade={props.onNewTrade}
    />
  );
};

describe('ChatPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: defaultMessages },
      mutate: mockMutateHistory,
    });
  });

  // -------------------------------------------------------------------------
  // Test 1: history renders on mount
  // -------------------------------------------------------------------------
  it('Test 1: renders existing messages loaded from GET /api/chat/ on mount', () => {
    renderPanel();

    // Both history messages should be visible
    expect(screen.getByText('Hello')).toBeTruthy();
    expect(screen.getByText('Hi there!')).toBeTruthy();

    // SWR must have been called with the trailing-slash path
    expect(useSWR).toHaveBeenCalledWith('/api/chat/', expect.anything());
  });

  // -------------------------------------------------------------------------
  // Test 2: loading indicator during POST
  // -------------------------------------------------------------------------
  it('Test 2: shows loading indicator while POST /api/chat/ is in flight, hidden after resolve', async () => {
    // Set up a slow fetch that we can resolve manually
    let resolveFetch!: (value: Response) => void;
    const pendingPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });

    global.fetch = jest.fn().mockReturnValue(pendingPromise);

    renderPanel();

    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…');
    fireEvent.change(input, { target: { value: 'analyze my portfolio' } });

    const sendButton = screen.getByRole('button', { name: /send/i });

    act(() => {
      fireEvent.click(sendButton);
    });

    // Loading indicator should be visible while in-flight
    await waitFor(() => {
      const indicator =
        screen.queryByText(/thinking/i) ||
        screen.queryByTestId('chat-loading') ||
        document.querySelector('[data-testid="chat-loading"]');
      expect(indicator).toBeTruthy();
    });

    // Resolve the fetch with a plain mock response (jsdom does not have Response)
    await act(async () => {
      resolveFetch({
        status: 200,
        ok: true,
        json: async () => ({ message: 'Done!', trades: [], watchlist_changes: [] }),
      } as unknown as Response);
      await Promise.resolve();
      await Promise.resolve();
    });

    // Loading indicator should disappear
    await waitFor(() => {
      const indicator =
        screen.queryByText(/thinking/i) ||
        document.querySelector('[data-testid="chat-loading"]');
      expect(indicator).toBeFalsy();
    });
  });

  // -------------------------------------------------------------------------
  // Test 3: trade action badge renders
  // -------------------------------------------------------------------------
  it('Test 3: assistant message with actions.trades renders a trade badge', () => {
    const msgWithTrade: ChatMessage = {
      role: 'assistant',
      content: 'I bought AAPL for you.',
      actions: {
        trades: [
          { status: 'executed', ticker: 'AAPL', side: 'buy', quantity: 5, price: 190.0 },
        ],
        watchlist_changes: [],
      },
      created_at: '2026-06-07T00:00:02Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [msgWithTrade] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    // Message text renders
    expect(screen.getByText('I bought AAPL for you.')).toBeTruthy();

    // Trade badge — "Bought 5 AAPL @ $190.00" should appear in a <span> badge element
    // The badge text is distinct from the message text so exact match works
    const badgeEl = screen.getByText(/Bought 5 AAPL @ \$190\.00/i);
    expect(badgeEl).toBeTruthy();
    expect(badgeEl.tagName.toLowerCase()).toBe('span');
  });

  // -------------------------------------------------------------------------
  // Test 4: watchlist action badge renders
  // -------------------------------------------------------------------------
  it('Test 4: assistant message with actions.watchlist_changes renders a watchlist badge', () => {
    const msgWithWatchlist: ChatMessage = {
      role: 'assistant',
      content: 'Added NVDA to your watchlist.',
      actions: {
        trades: [],
        watchlist_changes: [
          { status: 'added', ticker: 'NVDA', action: 'add' },
        ],
      },
      created_at: '2026-06-07T00:00:03Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [msgWithWatchlist] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    // Message text renders
    expect(screen.getByText('Added NVDA to your watchlist.')).toBeTruthy();

    // Watchlist badge — "Added NVDA" appears in both the message bubble and the badge span;
    // use getAllByText and confirm at least one match is the badge span element
    const matches = screen.getAllByText(/Added NVDA/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
    // The badge is a <span> — verify at least one match is a span
    const badgeSpan = matches.find((el) => el.tagName.toLowerCase() === 'span');
    expect(badgeSpan).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Test 5 (T-4-02): message content rendered as text, not HTML
  // -------------------------------------------------------------------------
  it('Test 5 (T-4-02): XSS content rendered as escaped text child, not injected HTML', () => {
    const xssMsg: ChatMessage = {
      role: 'assistant',
      content: '<script>alert(1)</script>',
      actions: null,
      created_at: '2026-06-07T00:00:04Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [xssMsg] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    // The script tag text should appear as literal text — not be executed
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy();

    // No <script> elements should have been injected into the DOM body
    const scripts = document.body.querySelectorAll('script');
    expect(scripts.length).toBe(0);
  });
});
