/**
 * ReplayStatus.test.tsx — market-replay indicators (D3 §3).
 *
 * Pure helpers:  isReplayActive (strict active === true gate, incl. foreign
 *                payloads a blanket SWR mock hands back), replayProgressPct
 *                (0..100 clamp + rounding, degenerate inputs)
 * ReplayBadge:   ZERO render on undefined / {active:false} / foreign payload;
 *                amber chip with the "回放 {date} · {i}/{n}" i18n copy when
 *                active; SWR wiring (key + 10s refresh); zh copy
 * ReplayBanner:  zero render inactive; window / current day / progress bar /
 *                loop chip when active; finished state copy (prices frozen)
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import {
  ReplayBadge,
  ReplayBanner,
  REPLAY_STATUS_KEY,
  REPLAY_REFRESH_MS,
  isReplayActive,
  replayProgressPct,
} from '@/components/ReplayStatus';
import type { ReplayStatusActive } from '@/types/market';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

// Mirrors the real backend payload: day_index is 0-BASED (first day == 0,
// pinned by backend/tests/test_replay_endpoint.py) — the UI renders
// day_index + 1, so index 2 displays as day 3/20.
const activeStatus = (over: Partial<ReplayStatusActive> = {}): ReplayStatusActive => ({
  active: true,
  from: '2020-03-02',
  to: '2020-03-27',
  current_date: '2020-03-16',
  day_index: 2,
  total_days: 20,
  seconds_per_day: 120,
  loop: true,
  finished: false,
  source_hint: 'sample',
  ...over,
});

const zhProfile = {
  market: 'cn',
  currency_symbol: '¥',
  locale: 'zh-CN',
  lot_size: 100,
  up_is_red: true,
  names: {},
};

function mockReplay(replay: unknown, profile?: Record<string, unknown>) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === REPLAY_STATUS_KEY) return { data: replay };
    if (key === '/api/market/profile' && profile) return { data: profile };
    return { data: undefined };
  }) as never);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockReplay(undefined);
});

describe('replay helpers (D3 §3)', () => {
  it('isReplayActive gates strictly on active === true (foreign payloads read inactive)', () => {
    expect(isReplayActive(undefined)).toBe(false);
    expect(isReplayActive(null)).toBe(false);
    expect(isReplayActive({ active: false })).toBe(false);
    // A blanket SWR mock can hand every hook the same session payload — the
    // guard must reject anything without a literal active === true.
    expect(
      isReplayActive({ state: 'open', session_id: 3, next_transition_at: null } as never)
    ).toBe(false);
    expect(isReplayActive({ active: 'true' } as never)).toBe(false);
    expect(isReplayActive(activeStatus())).toBe(true);
  });

  it('replayProgressPct clamps to 0..100, rounds, and zeroes degenerate inputs', () => {
    expect(replayProgressPct(3, 20)).toBe(15);
    expect(replayProgressPct(20, 20)).toBe(100);
    expect(replayProgressPct(0, 20)).toBe(0);
    expect(replayProgressPct(1, 3)).toBe(33);
    expect(replayProgressPct(25, 20)).toBe(100); // over-window clamps
    expect(replayProgressPct(-1, 20)).toBe(0);
    expect(replayProgressPct(3, 0)).toBe(0);
    expect(replayProgressPct(Number.NaN, 20)).toBe(0);
    expect(replayProgressPct(3, Number.NaN)).toBe(0);
  });
});

describe('ReplayBadge (D3 §3)', () => {
  it('renders NOTHING while the status is loading (data undefined)', () => {
    mockReplay(undefined);
    const { container } = render(<ReplayBadge />);
    expect(screen.queryByTestId('replay-badge')).toBeNull();
    expect(container.innerHTML).toBe('');
  });

  it('renders NOTHING when the endpoint reports {active:false} or a foreign payload', () => {
    mockReplay({ active: false });
    const { container, unmount } = render(<ReplayBadge />);
    expect(screen.queryByTestId('replay-badge')).toBeNull();
    expect(container.innerHTML).toBe('');
    unmount();

    // legacy blanket-mock shape (session snapshot) → still zero render
    mockReplay({ state: 'open', session_id: 1, next_transition_at: null });
    const { container: c2 } = render(<ReplayBadge />);
    expect(screen.queryByTestId('replay-badge')).toBeNull();
    expect(c2.innerHTML).toBe('');
  });

  it('active → amber chip with the "Replay {date} · {i}/{n}" copy', () => {
    mockReplay(activeStatus());
    render(<ReplayBadge />);

    const badge = screen.getByTestId('replay-badge');
    expect(badge.textContent).toBe('Replay 2020-03-16 · 3/20');
    expect(badge.className).toContain('text-terminal-amber');
    expect(badge.getAttribute('data-finished')).toBe('false');
  });

  it('polls GET /api/market/replay on a 10s SWR refresh interval', () => {
    mockReplay(activeStatus());
    render(<ReplayBadge />);

    expect(mockUseSWR).toHaveBeenCalledWith(
      REPLAY_STATUS_KEY,
      expect.any(Function),
      expect.objectContaining({ refreshInterval: REPLAY_REFRESH_MS })
    );
    expect(REPLAY_STATUS_KEY).toBe('/api/market/replay');
    expect(REPLAY_REFRESH_MS).toBe(10_000);
  });

  it('zh: renders the contract copy 回放 {date} · {i}/{n}', () => {
    mockReplay(activeStatus(), zhProfile);
    render(<ReplayBadge />);

    expect(screen.getByTestId('replay-badge').textContent).toBe('回放 2020-03-16 · 3/20');
  });
});

describe('ReplayBanner (D3 §3)', () => {
  it('renders NOTHING when inactive (undefined and {active:false})', () => {
    mockReplay(undefined);
    const { container, unmount } = render(<ReplayBanner />);
    expect(screen.queryByTestId('replay-banner')).toBeNull();
    expect(container.innerHTML).toBe('');
    unmount();

    mockReplay({ active: false });
    const { container: c2 } = render(<ReplayBanner />);
    expect(screen.queryByTestId('replay-banner')).toBeNull();
    expect(c2.innerHTML).toBe('');
  });

  it('active → window, current day, progress bar, and the loop chip', () => {
    mockReplay(activeStatus());
    render(<ReplayBanner />);

    expect(screen.getByTestId('replay-banner')).toBeTruthy();
    expect(screen.getByTestId('replay-banner-window').textContent).toBe(
      '2020-03-02 → 2020-03-27'
    );
    expect(screen.getByTestId('replay-banner-day').textContent).toBe(
      'Day 3/20 · 2020-03-16'
    );

    const progress = screen.getByTestId('replay-banner-progress');
    expect(progress.getAttribute('data-pct')).toBe('15');
    expect(progress.getAttribute('aria-valuenow')).toBe('15');
    expect(
      (screen.getByTestId('replay-banner-progress-fill') as HTMLElement).style.width
    ).toBe('15%');

    expect(screen.getByTestId('replay-banner-mode').textContent).toBe('Loop');
    expect(screen.queryByTestId('replay-banner-finished')).toBeNull();
    expect(screen.getByTestId('replay-banner').getAttribute('data-finished')).toBe('false');
  });

  it('no-loop replays show the Once chip', () => {
    mockReplay(activeStatus({ loop: false }));
    render(<ReplayBanner />);
    expect(screen.getByTestId('replay-banner-mode').textContent).toBe('Once');
  });

  it('finished → "prices frozen" copy and a full progress bar', () => {
    // A finished no-loop replay freezes on the LAST day: 0-based index 19
    // of 20 — the backend never emits day_index == total_days.
    mockReplay(activeStatus({ loop: false, finished: true, day_index: 19, current_date: '2020-03-27' }));
    render(<ReplayBanner />);

    expect(screen.getByTestId('replay-banner').getAttribute('data-finished')).toBe('true');
    expect(screen.getByTestId('replay-banner-finished').textContent).toBe(
      'Replay finished (prices frozen)'
    );
    expect(screen.getByTestId('replay-banner-progress').getAttribute('data-pct')).toBe('100');
  });

  it('zh finished state carries the contract copy 回放已结束（价格冻结）', () => {
    mockReplay(activeStatus({ loop: false, finished: true }), zhProfile);
    render(<ReplayBanner />);

    expect(screen.getByTestId('replay-banner-finished').textContent).toBe(
      '回放已结束（价格冻结）'
    );
    expect(screen.getByTestId('replay-banner-day').textContent).toBe('第 3/20 天 · 2020-03-16');
  });
});

describe('real-endpoint payload shape (anti-drift)', () => {
  // EXACTLY the first-day payload the backend serves — pinned verbatim by
  // backend/tests/test_replay_endpoint.py (day_index == 0 on day one). If
  // either side changes the day_index base, this test breaks first.
  const firstDayPayload = {
    active: true,
    from: '2026-06-01',
    to: '2026-06-02',
    current_date: '2026-06-01',
    day_index: 0,
    total_days: 2,
    seconds_per_day: 30.0,
    loop: false,
    finished: false,
    source_hint: 'sample',
  } as const;

  it('badge renders the first replay day as 1/{n}, never 0/{n}', () => {
    mockReplay(firstDayPayload);
    render(<ReplayBadge />);
    expect(screen.getByTestId('replay-badge').textContent).toBe('Replay 2026-06-01 · 1/2');
  });

  it('banner renders day 1/{n} with 1-day-of-2 progress (50%)', () => {
    mockReplay(firstDayPayload);
    render(<ReplayBanner />);
    expect(screen.getByTestId('replay-banner-day').textContent).toBe('Day 1/2 · 2026-06-01');
    expect(screen.getByTestId('replay-banner-progress').getAttribute('data-pct')).toBe('50');
  });

  it('last day (0-based index total_days - 1) renders {n}/{n} and 100%', () => {
    mockReplay({ ...firstDayPayload, current_date: '2026-06-02', day_index: 1, finished: true });
    render(<ReplayBanner />);
    expect(screen.getByTestId('replay-banner-day').textContent).toBe('Day 2/2 · 2026-06-02');
    expect(screen.getByTestId('replay-banner-progress').getAttribute('data-pct')).toBe('100');
  });
});
