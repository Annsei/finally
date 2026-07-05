/**
 * StatusBar tests (Batch 3 — bottom status strip):
 * Test 1: with no ticks, feed latency shows the placeholder and muted color
 * Test 2: a fresh tick shows a green sub-3s age; a stale one goes red
 * Test 3: the clock renders an HH:MM:SS time
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { usePriceStore } from '@/stores/priceStore';
import StatusBar from '@/components/StatusBar';

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
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
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
});
