/**
 * StrategyBadge.test.tsx — P2 §7/§8 chat strategy actions.
 *
 * Test 1: created outcome renders strategy-badge-created (purple family)
 * Test 2: deployed outcome renders strategy-badge-deployed
 * Test 3: paused outcome renders strategy-badge-paused
 * Test 4: failed outcome renders strategy-badge-failed with the error
 * Test 5: completed backtest outcome renders compact stats + Run Library note
 * Test 6: kind='strategy' messages get the purple KIND_BORDER + label
 * Test 7: a POST response whose only actions are strategies fires onNewTrade
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import type { ChatMessage, StrategyOutcome } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
import ChatPanel, { KIND_BORDER } from '@/components/ChatPanel';
import { useUiStore } from '@/stores/uiStore';

const mockMutateHistory = jest.fn();

const messageWith = (strategies: StrategyOutcome[], content = 'Done.'): ChatMessage => ({
  role: 'assistant',
  content,
  actions: { trades: [], watchlist_changes: [], strategies },
  created_at: '2026-07-07T00:00:00Z',
});

const mockHistory = (messages: ChatMessage[]) => {
  (useSWR as jest.Mock).mockReturnValue({
    data: { messages },
    mutate: mockMutateHistory,
  });
};

const renderPanel = (onNewTrade?: () => void) =>
  render(<ChatPanel open onToggle={jest.fn()} onNewTrade={onNewTrade} />);

describe('StrategyBadge', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    useUiStore.setState({ chatDraft: '', pendingChatMessage: null });
  });

  it('Test 1: created outcome renders the created badge in the purple family', () => {
    mockHistory([
      messageWith([
        { status: 'created', action: 'create', strategy_id: 's1', name: 'Golden Cross', ticker: 'NVDA' },
      ]),
    ]);
    renderPanel();

    const badge = screen.getByTestId('strategy-badge-created');
    expect(badge.textContent).toBe('Strategy created: Golden Cross (NVDA)');
    expect(badge.tagName.toLowerCase()).toBe('span');
    expect(badge.style.border).toContain('rgb(117, 57, 145)'); // #753991
  });

  it('Test 2: deployed outcome renders the deployed badge', () => {
    mockHistory([
      messageWith([{ status: 'deployed', action: 'deploy', strategy_id: 's1', name: 'Golden Cross' }]),
    ]);
    renderPanel();

    expect(screen.getByTestId('strategy-badge-deployed').textContent).toBe(
      'Strategy deployed: Golden Cross'
    );
    expect(screen.queryByTestId('strategy-badge-created')).not.toBeInTheDocument();
  });

  it('Test 3: paused outcome renders the paused badge (muted)', () => {
    mockHistory([
      messageWith([{ status: 'paused', action: 'pause', strategy_id: 's1', name: 'Golden Cross' }]),
    ]);
    renderPanel();

    const badge = screen.getByTestId('strategy-badge-paused');
    expect(badge.textContent).toBe('Strategy paused: Golden Cross');
    expect(badge.style.color).toBe('rgb(139, 148, 158)'); // #8b949e
  });

  it('Test 4: failed outcome renders the failed badge with the error text', () => {
    mockHistory([
      messageWith([
        { status: 'failed', action: 'deploy', name: 'Golden Cross', error: 'at least one exit required' },
      ]),
    ]);
    renderPanel();

    expect(screen.getByTestId('strategy-badge-failed').textContent).toBe(
      'Strategy failed: Golden Cross — at least one exit required'
    );
  });

  it('Test 5: completed backtest outcome renders compact stats and the Run Library note', () => {
    mockHistory([
      messageWith([
        {
          status: 'completed',
          action: 'backtest',
          strategy_id: 's1',
          name: 'Golden Cross',
          ticker: 'NVDA',
          run_id: 'r1',
          stats: {
            total_return_pct: 4.31,
            buy_hold_return_pct: -6.02,
            max_drawdown_pct: 3.87,
            final_equity: 10431.22,
            fires: 6,
            round_trips: 6,
            win_rate: 0.67,
            avg_win: 141.02,
            avg_loss: -80.55,
            profit_factor: 2.33,
            commission_paid: 0,
            rejections: { insufficient_cash: 0 },
          },
        },
      ]),
    ]);
    renderPanel();

    const badge = screen.getByTestId('strategy-badge-backtest');
    expect(badge.textContent).toBe(
      'Backtest Golden Cross: +4.3% (B&H -6.0%) · 6 trades · win 67% · saved to Runs'
    );
    expect(badge.style.color).toBe('rgb(32, 157, 215)'); // #209dd7, aligned with BacktestBadge
    // run_id present → the 'saved to Runs' tail deep-links to /run?id=X
    const link = badge.querySelector('a');
    expect(link).toBeTruthy();
    expect(link!.getAttribute('href')).toContain('/run');
    expect(link!.getAttribute('href')).toContain('id=r1');
    expect(link!.textContent).toBe('saved to Runs');
  });

  it('Test 5b: a completed backtest without run_id keeps the tail as plain text', () => {
    mockHistory([
      messageWith([
        {
          status: 'completed',
          action: 'backtest',
          strategy_id: 's1',
          name: 'Golden Cross',
          ticker: 'NVDA',
          stats: {
            total_return_pct: 4.31,
            buy_hold_return_pct: -6.02,
            max_drawdown_pct: 3.87,
            final_equity: 10431.22,
            fires: 6,
            round_trips: 6,
            win_rate: 0.67,
            avg_win: 141.02,
            avg_loss: -80.55,
            profit_factor: 2.33,
            commission_paid: 0,
            rejections: { insufficient_cash: 0 },
          },
        },
      ]),
    ]);
    renderPanel();

    const badge = screen.getByTestId('strategy-badge-backtest');
    expect(badge.textContent).toBe(
      'Backtest Golden Cross: +4.3% (B&H -6.0%) · 6 trades · win 67% · saved to Runs'
    );
    expect(badge.querySelector('a')).toBeNull();
  });

  it("Test 6: kind='strategy' messages carry the purple border and translated label", () => {
    expect(KIND_BORDER.strategy).toBe('#753991');
    const msg: ChatMessage = {
      role: 'assistant',
      content: 'Entered NVDA per Golden Cross.',
      kind: 'strategy',
      actions: null,
      created_at: '2026-07-07T00:00:00Z',
    };
    mockHistory([msg]);
    renderPanel();

    const label = screen.getByTestId('chat-kind-strategy');
    expect(label.textContent).toBe('Strategy');
    expect(label.style.color).toBe('rgb(117, 57, 145)');
  });

  it('Test 7: a response with only strategies actions triggers onNewTrade', async () => {
    mockHistory([]);
    const onNewTrade = jest.fn();
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        message: 'Created and backtested.',
        trades: [],
        watchlist_changes: [],
        strategies: [{ status: 'created', name: 'Golden Cross', ticker: 'NVDA' }],
      }),
    });

    renderPanel(onNewTrade);
    fireEvent.change(screen.getByPlaceholderText('Ask FinAlly about your portfolio…'), {
      target: { value: 'create a golden cross strategy' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /send/i }));
    });

    await waitFor(() => expect(onNewTrade).toHaveBeenCalledTimes(1));
    expect(mockMutateHistory).toHaveBeenCalled();
  });
});
