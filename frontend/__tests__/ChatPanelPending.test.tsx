/**
 * ChatPanelPending.test.tsx — P1 §2 ChatPanel increments:
 * uiStore-backed draft + one-shot pendingChatMessage auto-send.
 *
 * Test 1: pendingChatMessage set before mount → auto-POSTed as a user
 *         message, store slot cleared back to null
 * Test 2: default null → no request fired (zero behavior difference)
 * Test 3: the input draft is controlled by uiStore.chatDraft and survives a
 *         remount (cross-page persistence); sending clears it
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { useUiStore } from '@/stores/uiStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import useSWR from 'swr';
import ChatPanel from '@/components/ChatPanel';

const mockMutateHistory = jest.fn();

const renderPanel = () =>
  render(<ChatPanel open={true} onToggle={jest.fn()} />);

describe('ChatPanel pending message + draft (P1)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useUiStore.setState({
      portfolioTab: 'positions',
      backtestPrefill: null,
      chatOpen: true,
      chatDraft: '',
      pendingChatMessage: null,
    });
    (useSWR as jest.Mock).mockReturnValue({
      data: { messages: [] },
      mutate: mockMutateHistory,
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ message: 'Done!', trades: [], watchlist_changes: [] }),
    } as unknown as Response);
  });

  it('Test 1: a pending message auto-sends on mount and clears the one-shot slot', async () => {
    useUiStore.setState({ pendingChatMessage: 'Analyze AAPL for me' });

    renderPanel();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/chat/',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ message: 'Analyze AAPL for me' }),
        })
      );
    });
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(useUiStore.getState().pendingChatMessage).toBeNull();
    await waitFor(() => expect(mockMutateHistory).toHaveBeenCalled());
  });

  it('Test 1b: a whitespace-only pending message sends nothing but still frees the slot', async () => {
    useUiStore.setState({ pendingChatMessage: '   ' });

    renderPanel();

    await act(async () => {
      await Promise.resolve();
    });
    expect(global.fetch).not.toHaveBeenCalled();
    // The one-shot slot must not stay occupied forever
    expect(useUiStore.getState().pendingChatMessage).toBeNull();
  });

  it('Test 1c: StrictMode double-invoked mount effects still consume exactly once', async () => {
    useUiStore.setState({ pendingChatMessage: 'Analyze NVDA for me' });

    render(
      <React.StrictMode>
        <ChatPanel open={true} onToggle={jest.fn()} />
      </React.StrictMode>
    );

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(useUiStore.getState().pendingChatMessage).toBeNull();
  });

  it('Test 2: with the default null slot, nothing is sent', async () => {
    renderPanel();

    // allow any (incorrect) effect work to flush
    await act(async () => {
      await Promise.resolve();
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('Test 3: draft is uiStore-controlled, survives remount, clears on send', async () => {
    const first = renderPanel();
    const input = screen.getByPlaceholderText('Ask FinAlly about your portfolio…') as HTMLInputElement;

    fireEvent.change(input, { target: { value: 'half-typed thought' } });
    expect(useUiStore.getState().chatDraft).toBe('half-typed thought');

    // Simulate navigating away and back: unmount, then mount a fresh panel
    first.unmount();
    renderPanel();
    const input2 = screen.getByPlaceholderText('Ask FinAlly about your portfolio…') as HTMLInputElement;
    expect(input2.value).toBe('half-typed thought');

    // Sending clears the draft in the store
    fireEvent.click(screen.getByRole('button', { name: /send/i }));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    expect(useUiStore.getState().chatDraft).toBe('');
    expect(input2.value).toBe('');
  });
});
