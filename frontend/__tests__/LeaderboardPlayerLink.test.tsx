/**
 * LeaderboardPlayerLink.test.tsx — leaderboard → /player links (P4 §4).
 *
 * The name cell wraps the trader name in a <Link> to /player?u=<id> with
 * testid player-link-<id>, while the name stays the link's own text node so
 * the baseline Leaderboard assertions (textContent/getByText) keep passing.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

jest.mock('@/lib/reload', () => ({
  __esModule: true,
  hardReload: jest.fn(),
}));

import Leaderboard from '@/components/Leaderboard';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const board = {
  season: { id: 2, started_at: '2026-07-06T00:00:00Z' },
  entries: [
    { user_id: 'fiona', name: 'Fiona', total_value: 11250.5, return_pct: 12.51, rank: 1 },
    { user_id: 'default', name: 'Guest', total_value: 9980.0, return_pct: -0.2, rank: 2 },
  ],
};

beforeEach(() => {
  jest.clearAllMocks();
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/leaderboard') return { data: board, mutate: jest.fn() };
    if (key === '/api/auth/me') return { data: { user: { id: 'fiona', name: 'Fiona' } } };
    return { data: undefined };
  }) as never);
});

describe('Leaderboard player links (P4 §4)', () => {
  it('every entry name is wrapped in a player-link-<id> anchor to /player?u=<id>', () => {
    render(<Leaderboard />);

    const fiona = screen.getByTestId('player-link-fiona');
    expect(fiona.tagName).toBe('A');
    expect(fiona.getAttribute('href')).toContain('/player');
    expect(fiona.getAttribute('href')).toContain('u=fiona');

    const guest = screen.getByTestId('player-link-default');
    expect(guest.getAttribute('href')).toContain('u=default');
  });

  it('the trader name stays the link text node (baseline getByText compatible)', () => {
    render(<Leaderboard />);
    expect(screen.getByTestId('player-link-fiona').textContent).toBe('Fiona');
    expect(screen.getByText('Fiona')).toBeTruthy();
  });

  it('the (you) marker renders in the cell but OUTSIDE the link', () => {
    render(<Leaderboard />);
    const row = screen.getByTestId('leaderboard-row-fiona');
    expect(row.textContent).toContain('Fiona');
    expect(row.textContent).toContain('(you)');
    expect(screen.getByTestId('player-link-fiona').textContent).not.toContain('(you)');
  });
});
