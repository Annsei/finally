/**
 * StatusBar tests (Batch 3 status strip + M3.1 session badge):
 * Test 1: with no ticks, feed latency shows the placeholder and muted color
 * Test 2: a fresh tick shows a green sub-3s age; a stale one goes red
 * Test 3: the clock renders an HH:MM:SS time
 * Test 4: open session renders OPEN with a countdown to the close
 * Test 5: closed session renders CLOSED with a countdown to the reopen
 * Test 6: 24/7 mode (null next_transition_at) renders the SIM 24/7 label
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import StatusBar from '@/components/StatusBar';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const tickAt = (timestamp: number) => ({
  ticker: 'AAPL',
  price: 190,
  previous_price: 189.9,
  timestamp,
  change: 0.1,
  change_percent: 0.05,
  direction: 'up' as const,
});

describe('StatusBar', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    mockUseSWR.mockReturnValue({ data: undefined } as any);
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('Test 1: no ticks → placeholder latency, muted color', () => {
    render(<StatusBar />);

    const feed = screen.getByTestId('status-feed-latency');
    expect(feed.textContent).toBe('Feed: —');
    expect(feed.className).toContain('text-terminal-muted');
  });

  it('Test 2: fresh tick is green, stale tick turns red after time passes', () => {
    render(<StatusBar />);

    act(() => {
      usePriceStore.setState({ prices: { AAPL: tickAt(Date.now() / 1000) } });
    });
    // advance one interval so `now` refreshes
    act(() => {
      jest.advanceTimersByTime(1000);
    });

    let feed = screen.getByTestId('status-feed-latency');
    expect(feed.className).toContain('text-terminal-up');

    // 15s with no new ticks → red
    act(() => {
      jest.advanceTimersByTime(15000);
    });
    feed = screen.getByTestId('status-feed-latency');
    expect(feed.className).toContain('text-terminal-down');
    expect(feed.textContent).toMatch(/Feed: \d+s ago/);
  });

  it('Test 3: clock renders HH:MM:SS', () => {
    render(<StatusBar />);

    expect(screen.getByTestId('status-clock').textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it('Test 4: open session renders OPEN with a countdown to the close', () => {
    mockUseSWR.mockReturnValue({
      data: {
        state: 'open',
        session_id: 3,
        state_since: Date.now() / 1000 - 100,
        next_transition_at: Date.now() / 1000 + 125, // 2:05 from now
        now: Date.now() / 1000,
      },
    } as any);

    render(<StatusBar />);

    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('open');
    expect(badge.textContent).toContain('OPEN');
    expect(badge.textContent).toMatch(/closes in 2:0[0-5]/);
    expect(badge.className).toContain('text-terminal-up');
  });

  it('Test 5: closed session renders CLOSED with a countdown to the reopen', () => {
    mockUseSWR.mockReturnValue({
      data: {
        state: 'closed',
        session_id: 3,
        state_since: Date.now() / 1000 - 10,
        next_transition_at: Date.now() / 1000 + 65, // 1:05 from now
        now: Date.now() / 1000,
      },
    } as any);

    render(<StatusBar />);

    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('closed');
    expect(badge.textContent).toContain('CLOSED');
    expect(badge.textContent).toMatch(/opens in 1:0[0-5]/);
    expect(badge.className).toContain('text-terminal-down');
  });

  it('Test 6: 24/7 mode renders the SIM 24/7 label', () => {
    mockUseSWR.mockReturnValue({
      data: {
        state: 'open',
        session_id: 1,
        state_since: 0,
        next_transition_at: null,
        now: Date.now() / 1000,
      },
    } as any);

    render(<StatusBar />);

    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('always-open');
    expect(badge.textContent).toBe('SIM 24/7');
  });
});
