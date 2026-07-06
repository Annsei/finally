/**
 * Leaderboard tests (M4.2/4.3):
 * Test 1: entries render ranked with colored returns; own row highlighted
 * Test 2: season reset requires a second confirming click, then POSTs
 * Test 3: failed reset surfaces the error inline
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

jest.mock('@/lib/reload', () => ({
  __esModule: true,
  hardReload: jest.fn(),
}));

import { hardReload } from '@/lib/reload';

import Leaderboard from '@/components/Leaderboard';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

const board = {
  season: { id: 2, started_at: '2026-07-06T00:00:00Z' },
  entries: [
    { user_id: 'fiona', name: 'Fiona', total_value: 11250.5, return_pct: 12.51, rank: 1 },
    { user_id: 'default', name: 'Guest', total_value: 9980.0, return_pct: -0.2, rank: 2 },
  ],
};

const mockByKey = (meUserId: string | null) => {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === '/api/leaderboard') return { data: board, mutate: jest.fn() } as any;
    if (key === '/api/auth/me')
      return { data: meUserId ? { user: { id: meUserId, name: meUserId } } : undefined } as any;
    return { data: undefined } as any;
  }) as any);
};

describe('Leaderboard', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  it('Test 1: entries render ranked with colored returns and own-row highlight', () => {
    mockByKey('fiona');

    render(<Leaderboard />);

    expect(screen.getByText(/Season 2/)).toBeInTheDocument();

    const row1 = screen.getByTestId('leaderboard-row-fiona');
    expect(row1.textContent).toContain('Fiona');
    expect(row1.textContent).toContain('(you)');
    expect(row1.textContent).toContain('$11,250.50');
    expect(row1.textContent).toContain('+12.51%');
    expect(row1.className).toContain('border-l-terminal-accent');

    const row2 = screen.getByTestId('leaderboard-row-default');
    expect(row2.textContent).toContain('Guest');
    expect(row2.textContent).toContain('-0.20%');
    expect(row2.className).not.toContain('border-l-terminal-accent');
  });

  it('Test 2: reset needs a confirming second click, then POSTs and reloads', async () => {
    mockByKey('fiona');
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    render(<Leaderboard />);

    const button = screen.getByTestId('season-reset');
    fireEvent.click(button);
    expect(button.textContent).toBe('Confirm reset?');
    expect(global.fetch).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(button);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/season/reset',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ confirm: true }),
      })
    );
    await waitFor(() => expect(hardReload).toHaveBeenCalled());
  });

  it('Test 3: failed reset surfaces the error inline', async () => {
    mockByKey(null);
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      json: async () => ({ error: 'Confirmation required' }),
    });

    render(<Leaderboard />);

    const button = screen.getByTestId('season-reset');
    fireEvent.click(button);
    await act(async () => {
      fireEvent.click(button);
    });

    await waitFor(() => {
      expect(screen.getByTestId('leaderboard-error').textContent).toBe('Confirmation required');
    });
  });
});
