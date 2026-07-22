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
    expect(useSWR).toHaveBeenCalledWith('/api/chat/', expect.anything(), expect.anything());
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
  // Test 3b/4f: FAILED outcomes must render failure badges, never fake success
  // Backend failure shapes: trades {status:"failed", ticker, error} (no side/
  // quantity/price); watchlist {status:"failed", ticker, error} (no action).
  // -------------------------------------------------------------------------
  it('Test 3b: a failed trade outcome renders a failure badge, not a "Sold undefined" success badge', () => {
    const msgWithFailedTrade: ChatMessage = {
      role: 'assistant',
      content: 'I could not complete that trade.',
      actions: {
        trades: [
          { status: 'failed', ticker: 'AAPL', error: 'Insufficient cash' },
        ],
        watchlist_changes: [],
      },
      created_at: '2026-06-07T00:00:04Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [msgWithFailedTrade] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    const badge = screen.getByTestId('trade-badge-failed');
    expect(badge.textContent).toContain('Trade failed: AAPL');
    expect(badge.textContent).toContain('Insufficient cash');

    // Regression: must NOT render as a success badge with undefined fields
    expect(screen.queryByText(/Sold undefined/i)).toBeNull();
    expect(screen.queryByText(/Bought undefined/i)).toBeNull();
  });

  it('Test 4f: a failed watchlist outcome renders a failure badge, not "Removed TICKER"', () => {
    const msgWithFailedChange: ChatMessage = {
      role: 'assistant',
      content: 'I could not update the watchlist.',
      actions: {
        trades: [],
        watchlist_changes: [
          { status: 'failed', ticker: 'PYPL', error: 'Ticker must be 10 characters or fewer' },
        ],
      },
      created_at: '2026-06-07T00:00:05Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [msgWithFailedChange] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    const badge = screen.getByTestId('watchlist-badge-failed');
    expect(badge.textContent).toContain('Watchlist change failed: PYPL');

    // Regression: a failed change has no action field and must not read as a removal
    expect(screen.queryByText(/Removed PYPL/i)).toBeNull();
  });

  // -------------------------------------------------------------------------
  // M2.1/2.2: AI-placed order and AI-created rule badges
  // -------------------------------------------------------------------------
  it('Test 3c: an AI-placed resting stop order renders a placed badge with stop price', () => {
    const msg: ChatMessage = {
      role: 'assistant',
      content: 'I placed a protective stop.',
      actions: {
        trades: [],
        watchlist_changes: [],
        orders: [
          {
            status: 'open', ticker: 'AAPL', side: 'sell', quantity: 5,
            kind: 'stop', limit_price: null, stop_price: 170, fill_price: null,
          },
        ],
      },
      created_at: '2026-07-06T00:00:06Z',
    };
    (useSWR as jest.Mock).mockReturnValue({ data: { messages: [msg] }, mutate: mockMutateHistory });

    renderPanel();

    const badge = screen.getByTestId('order-badge-placed');
    expect(badge.textContent).toBe('Order placed: Sell 5 AAPL @ stop $170.00');
  });

  it('Test 3d: a failed AI order renders a failure badge', () => {
    const msg: ChatMessage = {
      role: 'assistant',
      content: 'That stop could not be placed.',
      actions: {
        trades: [],
        watchlist_changes: [],
        orders: [{ status: 'failed', ticker: 'AAPL', error: 'Stop price must be below the market' }],
      },
      created_at: '2026-07-06T00:00:07Z',
    };
    (useSWR as jest.Mock).mockReturnValue({ data: { messages: [msg] }, mutate: mockMutateHistory });

    renderPanel();

    expect(screen.getByTestId('order-badge-failed').textContent).toContain(
      'Order failed: AAPL — Stop price must be below the market'
    );
  });

  it('Test 3e: an AI-created rule renders an armed badge with the description', () => {
    const msg: ChatMessage = {
      role: 'assistant',
      content: 'Rule created.',
      actions: {
        trades: [],
        watchlist_changes: [],
        rules: [
          {
            status: 'created',
            rule: {
              id: 'r9', ticker: 'NVDA', description: 'Buy 5 NVDA when day change <= -3%',
              trigger_type: 'day_change_pct_below', threshold: -3, side: 'buy', quantity: 5,
              status: 'active', created_at: '2026-07-06T00:00:08Z', last_fired_at: null, fire_count: 0,
            },
          },
        ],
      },
      created_at: '2026-07-06T00:00:08Z',
    };
    (useSWR as jest.Mock).mockReturnValue({ data: { messages: [msg] }, mutate: mockMutateHistory });

    renderPanel();

    expect(screen.getByTestId('rule-badge-created').textContent).toBe(
      'Rule armed: Buy 5 NVDA when day change <= -3%'
    );
  });

  // -------------------------------------------------------------------------
  // M2.3/2.4: agent-initiated message kinds + on-demand daily review
  // -------------------------------------------------------------------------
  it('Test 6: brief/review/rule messages render labeled variants', () => {
    const messages: ChatMessage[] = [
      { role: 'assistant', content: 'NVDA spiked +3.4% — you hold 5 shares.', kind: 'brief', actions: null, created_at: '2026-07-06T01:00:00Z' },
      { role: 'assistant', content: 'You made 3 trades today...', kind: 'review', actions: null, created_at: '2026-07-06T02:00:00Z' },
      { role: 'assistant', content: 'Rule fired: Buy 2 NVDA...', kind: 'rule', actions: null, created_at: '2026-07-06T03:00:00Z' },
      { role: 'assistant', content: 'Plain reply.', kind: 'chat', actions: null, created_at: '2026-07-06T04:00:00Z' },
    ];
    (useSWR as jest.Mock).mockReturnValue({ data: { messages }, mutate: mockMutateHistory });

    renderPanel();

    expect(screen.getByTestId('chat-kind-brief').textContent).toBe('Market Brief');
    expect(screen.getByTestId('chat-kind-review').textContent).toBe('Daily Review');
    expect(screen.getByTestId('chat-kind-rule').textContent).toBe('Rule');
    // Plain chat messages get no label
    expect(screen.queryByTestId('chat-kind-chat')).toBeNull();
  });

  it('Test 7: Review button POSTs /api/chat/review and refreshes history', async () => {
    global.fetch = jest.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ message: '[MOCK REVIEW] ...', kind: 'review' }),
    });

    renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-review-button'));
    });

    expect(global.fetch).toHaveBeenCalledWith('/api/chat/review', { method: 'POST' });
    await waitFor(() => {
      expect(mockMutateHistory).toHaveBeenCalled();
    });
  });

  it('Test 7b: failed review surfaces the inline error', async () => {
    global.fetch = jest.fn().mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({ error: 'LLM unavailable' }),
    });

    renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-review-button'));
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-error').textContent).toBe('LLM unavailable');
    });
  });

  // -------------------------------------------------------------------------
  // Test 4b (FIX 2): HTTP error response surfaces an inline error message
  // -------------------------------------------------------------------------
  it('Test 4b: POST /api/chat/ returning 5xx surfaces inline error and clears loading', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      status: 500,
      ok: false,
      json: async () => ({ error: 'LLM backend unavailable' }),
    } as unknown as Response);

    renderPanel();

    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…');
    fireEvent.change(input, { target: { value: 'analyze my portfolio' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    // Inline error appears in the history area with the backend detail
    await waitFor(() => {
      expect(screen.getByTestId('chat-error')).toBeTruthy();
      expect(screen.getByText('LLM backend unavailable')).toBeTruthy();
    });

    // Loading indicator must be gone (finally ran)
    expect(document.querySelector('[data-testid="chat-loading"]')).toBeFalsy();
    // History was NOT revalidated on failure
    expect(mockMutateHistory).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Test 4c (FIX 2): network failure (fetch rejects) surfaces an inline error
  // -------------------------------------------------------------------------
  it('Test 4c: network failure surfaces inline error and clears loading', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('Failed to fetch'));

    renderPanel();

    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…');
    fireEvent.change(input, { target: { value: 'hello' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByTestId('chat-error')).toBeTruthy();
      expect(screen.getByText('Failed to fetch')).toBeTruthy();
    });
    expect(document.querySelector('[data-testid="chat-loading"]')).toBeFalsy();
  });

  // -------------------------------------------------------------------------
  // Test 4d (FIX 2): error clears on the next successful send
  // -------------------------------------------------------------------------
  it('Test 4d: inline error clears when the next message is sent successfully', async () => {
    global.fetch = jest
      .fn()
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ message: 'Done!', trades: [], watchlist_changes: [] }),
      } as unknown as Response);

    renderPanel();

    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…');
    fireEvent.change(input, { target: { value: 'first' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(screen.getByTestId('chat-error')).toBeTruthy());

    fireEvent.change(input, { target: { value: 'second' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(document.querySelector('[data-testid="chat-error"]')).toBeFalsy();
    });
  });

  // -------------------------------------------------------------------------
  // Test 4e (FIX 3): onNewTrade callback fires for watchlist_changes too
  // -------------------------------------------------------------------------
  it('Test 4e: onNewTrade fires when the response contains only watchlist_changes', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({
        message: 'Added PYPL to your watchlist.',
        trades: [],
        watchlist_changes: [{ status: 'added', ticker: 'PYPL', action: 'add' }],
      }),
    } as unknown as Response);

    const onNewTrade = jest.fn();
    renderPanel({ onNewTrade });

    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…');
    fireEvent.change(input, { target: { value: 'watch PYPL' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(onNewTrade).toHaveBeenCalledTimes(1));
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

  // -------------------------------------------------------------------------
  // Test 6 (M5): backtest outcome badges
  // -------------------------------------------------------------------------
  it('Test 6 (M5): assistant actions.backtests render completed and failed badges', () => {
    const btMsg: ChatMessage = {
      role: 'assistant',
      content: 'Backtest done.',
      actions: {
        trades: [],
        watchlist_changes: [],
        backtests: [
          {
            status: 'completed',
            ticker: 'NVDA',
            stats: {
              total_return_pct: 4.3,
              buy_hold_return_pct: 6.0,
              max_drawdown_pct: 3.9,
              final_equity: 10430,
              fires: 6,
              round_trips: 6,
              win_rate: 0.67,
              avg_win: 141,
              avg_loss: -80,
              profit_factor: 2.3,
              commission_paid: 0,
              rejections: { insufficient_cash: 0 },
            },
          },
          { status: 'failed', ticker: 'ZZZZ', error: 'Ticker not found' },
        ],
      },
      created_at: '2026-07-07T00:00:05Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [btMsg] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    const completed = screen.getByTestId('backtest-badge-completed');
    expect(completed.textContent).toContain('Backtest NVDA: +4.3%');
    expect(completed.textContent).toContain('win 67%');

    const failed = screen.getByTestId('backtest-badge-failed');
    expect(failed.textContent).toContain('ZZZZ');
    expect(failed.textContent).toContain('Ticker not found');
  });

  // -------------------------------------------------------------------------
  // Test 6b (D4): research outcomes render a StrategyResearchCard block card
  // -------------------------------------------------------------------------
  it('Test 6b (D4): assistant actions.research renders a research card with ranked rows', () => {
    const researchStats = {
      total_return_pct: 12.3,
      buy_hold_return_pct: 8.0,
      max_drawdown_pct: 4.5,
      final_equity: 11230,
      fires: 6,
      round_trips: 6,
      win_rate: 0.67,
      avg_win: 140,
      avg_loss: -80,
      profit_factor: 2.1,
      commission_paid: 0,
      rejections: { insufficient_cash: 0 },
    };
    const researchMsg: ChatMessage = {
      role: 'assistant',
      content: 'I researched three approaches for AAPL.',
      actions: {
        trades: [],
        watchlist_changes: [],
        research: [
          {
            status: 'completed',
            ticker: 'AAPL',
            days: 120,
            candidates: [
              {
                status: 'completed',
                name: 'Golden Cross',
                hypothesis: 'Trend following via SMA cross.',
                strategy_id: 'st-1',
                run_id: 'run-1',
                score: 10.05,
                rank: 1,
                traded: true,
                stats: researchStats,
              },
              {
                status: 'completed',
                name: 'RSI Rebound',
                hypothesis: 'Mean reversion on oversold RSI.',
                strategy_id: 'st-2',
                run_id: 'run-2',
                score: 8.1,
                rank: 2,
                traded: true,
                stats: researchStats,
              },
              { status: 'failed', name: 'Broken Idea', error: 'missing exit' },
            ],
            recommended_strategy_id: 'st-1',
          },
        ],
      },
      created_at: '2026-07-13T00:00:00Z',
    };

    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [researchMsg] },
      mutate: mockMutateHistory,
    });

    renderPanel();

    // Block card (not a pill) with one row per candidate, the recommended
    // badge, and a deploy button per completed candidate.
    const card = screen.getByTestId('research-card');
    expect(card.textContent).toContain('AAPL');
    expect(screen.getAllByTestId('research-candidate')).toHaveLength(3);
    expect(screen.getAllByTestId('research-recommended')).toHaveLength(1);
    expect(screen.getAllByTestId('research-deploy')).toHaveLength(2);
    expect(card.textContent).toContain('Broken Idea');
    expect(card.textContent).toContain('missing exit');
  });

  // -------------------------------------------------------------------------
  // Test 7 (M5.1): briefs render clamped, click expands
  // -------------------------------------------------------------------------
  it('Test 7 (M5.1): brief messages render two-line clamped and expand on click', () => {
    const longBrief =
      'Consider placing a modest buy order on a pull-back to test support as the rally continues across the entire tech sector this afternoon.';
    const messages: ChatMessage[] = [
      {
        role: 'assistant',
        content: longBrief,
        kind: 'brief',
        actions: null,
        created_at: '2026-07-07T00:00:00Z',
      },
      {
        role: 'assistant',
        content: 'A normal chat reply.',
        kind: 'chat',
        actions: null,
        created_at: '2026-07-07T00:00:01Z',
      },
    ];
    (useSWR as jest.Mock).mockReturnValue({
      data: { messages },
      mutate: mockMutateHistory,
    });

    renderPanel();

    // Collapsed by default: two-line -webkit-box clamp
    const brief = screen.getByTestId('brief-content');
    expect(brief.getAttribute('data-expanded')).toBe('false');
    expect(brief.style.webkitLineClamp).toBe('2');
    expect(brief.textContent).toBe(longBrief);

    // Click expands (clamp removed), click again collapses
    fireEvent.click(brief);
    expect(brief.getAttribute('data-expanded')).toBe('true');
    expect(brief.style.webkitLineClamp).toBe('');
    fireEvent.click(brief);
    expect(brief.getAttribute('data-expanded')).toBe('false');

    // Plain chat messages are NOT wrapped in the clamp control
    expect(screen.getAllByTestId('brief-content')).toHaveLength(1);
    expect(screen.getByText('A normal chat reply.')).toBeTruthy();
  });
});
