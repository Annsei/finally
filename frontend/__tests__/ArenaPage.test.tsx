/**
 * ArenaPage.test.tsx — /arena competition page (P1 §7).
 *
 * The leaderboard mounts as the existing <Leaderboard/> component (stubbed
 * here — it has its own suite). Season history renders every season from
 * GET /api/seasons: in-progress marker for the live season, archived results
 * table (rank/name/final_value/return_pct) for ended seasons with the
 * champion highlighted in accent, and an i18n empty state.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
  useSWRConfig: jest.fn().mockReturnValue({ mutate: jest.fn() }),
}));

jest.mock('@/components/AppShell', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
  TRADE_REVALIDATE_KEYS: [
    '/api/portfolio/',
    '/api/portfolio/trades',
    '/api/portfolio/orders?status=open',
    '/api/rules',
    '/api/watchlist/',
  ],
}));

// Leaderboard has its own test suite — the arena contract is that the SAME
// component mounts zero-modification.
jest.mock('@/components/Leaderboard', () => ({
  __esModule: true,
  default: () => <div data-testid="leaderboard-stub" />,
}));

import ArenaPage from '@/pages/arena';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

function mockSeasons(seasons: unknown[] | undefined) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/seasons' && seasons !== undefined) {
      return { data: { seasons }, mutate: jest.fn() };
    }
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

describe('ArenaPage (P1 §7)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSeasons(undefined);
  });

  it('mounts the existing Leaderboard component and the seasons panel', () => {
    mockSeasons([]);
    render(<ArenaPage />);
    expect(screen.getByTestId('leaderboard-stub')).toBeTruthy();
    expect(screen.getByTestId('arena-seasons')).toBeTruthy();
  });

  it('renders the in-progress season with a marker and no results table', () => {
    mockSeasons([
      { id: 2, started_at: '2026-07-01T00:00:00', ended_at: null, results: null },
    ]);
    render(<ArenaPage />);
    const season = screen.getByTestId('arena-season-2');
    expect(season.textContent).toContain('Season 2');
    expect(screen.getByTestId('arena-season-current-2').textContent).toBe('In progress');
    expect(season.querySelector('table')).toBeNull();
  });

  it('renders ended-season results with the champion highlighted in accent', () => {
    mockSeasons([
      { id: 2, started_at: '2026-07-01T00:00:00', ended_at: null, results: null },
      {
        id: 1,
        started_at: '2026-06-01T00:00:00',
        ended_at: '2026-06-30T00:00:00',
        results: [
          { user_id: 'u1', name: 'Ada', final_value: 13750.5, return_pct: 37.51, rank: 1 },
          { user_id: 'u2', name: 'Bob', final_value: 9200, return_pct: -8, rank: 2 },
        ],
      },
    ]);
    render(<ArenaPage />);

    // ended season: no in-progress marker, results table renders
    expect(screen.queryByTestId('arena-season-current-1')).toBeNull();
    const champion = screen.getByTestId('arena-season-1-rank-1');
    expect(champion.textContent).toContain('Ada');
    expect(champion.textContent).toContain('$13,750.50'); // formatMoney
    expect(champion.textContent).toContain('+37.51%');
    expect(champion.className).toContain('border-l-terminal-accent');
    expect(champion.querySelector('.text-terminal-accent')).toBeTruthy();

    const second = screen.getByTestId('arena-season-1-rank-2');
    expect(second.textContent).toContain('Bob');
    expect(second.textContent).toContain('-8.00%');
    expect(second.className).not.toContain('border-l-terminal-accent');
    // negative return colored via the direction class
    expect(second.querySelector('.text-terminal-down')).toBeTruthy();
  });

  it('shows the i18n empty state when no seasons exist', () => {
    mockSeasons([]);
    render(<ArenaPage />);
    expect(screen.getByTestId('arena-seasons').textContent).toContain('No seasons yet.');
  });
});
