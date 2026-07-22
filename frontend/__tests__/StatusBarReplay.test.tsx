/**
 * StatusBarReplay.test.tsx — StatusBar replay chip integration (D3 §3).
 *
 * The chip is a PURE ADDITION: when replay is inactive (the default
 * deployment) the strip must render exactly its pre-D3 DOM — session badge,
 * shortcuts, feed latency, clock — with no replay-badge node at all.
 *
 * Test 1: default (all SWR data undefined) → no replay-badge, existing
 *         testids/copy intact (zero-render regression)
 * Test 2: {active:false} from the endpoint → still zero replay DOM while the
 *         session badge renders its usual OPEN countdown
 * Test 3: legacy blanket SWR mock (every key returns the session payload,
 *         the exact StatusBar.test.tsx recipe) → no replay-badge leaks
 * Test 4: active replay → amber chip with the badge copy, session badge and
 *         the rest of the strip unchanged
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import StatusBar from '@/components/StatusBar';
import { REPLAY_STATUS_KEY } from '@/components/ReplayStatus';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const sessionOpen = () => ({
  state: 'open',
  session_id: 3,
  state_since: Date.now() / 1000 - 100,
  next_transition_at: Date.now() / 1000 + 125,
  now: Date.now() / 1000,
});

const replayActive = () => ({
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
});

function mockKeys(byKey: Record<string, unknown>) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key in byKey) return { data: byKey[key] };
    return { data: undefined };
  }) as never);
}

describe('StatusBar × replay chip (D3 §3)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    usePriceStore.setState({ prices: {}, connectionStatus: 'disconnected' });
    mockUseSWR.mockReturnValue({ data: undefined } as never);
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('Test 1: default deployment (no replay data) → zero replay DOM, strip unchanged', () => {
    render(<StatusBar />);

    expect(screen.queryByTestId('replay-badge')).toBeNull();
    // the pre-D3 strip is intact
    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('always-open');
    expect(badge.textContent).toBe('SIM 24/7');
    expect(screen.getByTestId('status-feed-latency').textContent).toBe('Feed: —');
    expect(screen.getByTestId('status-clock').textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it('Test 2: endpoint reports {active:false} → no replay node, session badge untouched', () => {
    mockKeys({
      '/api/market/session': sessionOpen(),
      [REPLAY_STATUS_KEY]: { active: false },
    });
    render(<StatusBar />);

    expect(screen.queryByTestId('replay-badge')).toBeNull();
    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('open');
    expect(badge.textContent).toContain('OPEN');
    expect(badge.textContent).toMatch(/closes in 2:0[0-5]/);
  });

  it('Test 3: legacy blanket SWR mock (session payload for every key) → no replay-badge leaks', () => {
    // Exactly the StatusBar.test.tsx recipe: one mockReturnValue feeds BOTH
    // hooks. The session payload has no active===true, so the chip must stay
    // absent — this is what keeps the existing suite passing unmodified.
    mockUseSWR.mockReturnValue({ data: sessionOpen() } as never);
    render(<StatusBar />);

    expect(screen.queryByTestId('replay-badge')).toBeNull();
    expect(screen.getByTestId('session-badge').textContent).toContain('OPEN');
  });

  it('Test 4: active replay → amber chip beside the untouched session badge', () => {
    mockKeys({
      '/api/market/session': sessionOpen(),
      [REPLAY_STATUS_KEY]: replayActive(),
    });
    render(<StatusBar />);

    const chip = screen.getByTestId('replay-badge');
    expect(chip.textContent).toBe('Replay 2020-03-16 · 3/20');
    expect(chip.className).toContain('text-terminal-amber');

    // additive: the session badge still renders exactly as before
    const badge = screen.getByTestId('session-badge');
    expect(badge.getAttribute('data-state')).toBe('open');
    expect(badge.textContent).toContain('OPEN');
    expect(screen.getByTestId('status-feed-latency')).toBeTruthy();
    expect(screen.getByTestId('status-clock')).toBeTruthy();
  });
});
