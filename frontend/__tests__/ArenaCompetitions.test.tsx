/**
 * ArenaCompetitions.test.tsx — /arena timed private competitions (D2 §5).
 *
 * Create:    comp-name/comp-hours validation toasts (no request fired), POST
 *            /api/competitions payload, success → invite code + copy button,
 *            server error toast.
 * Join:      POST /api/competitions/join payload (uppercased), error toast on
 *            400/404, empty-code validation.
 * List:      comp-row-${id} name / status chip / member count; ended rows.
 * Countdown: comp-countdown-${id} ticks down every second on one shared
 *            interval (fake timers) and the interval is cleared on unmount.
 * Board:     row click expands comp-board-${id} (SWR-driven), rank/name/value/
 *            return% direction colours, ended final-standings marker, second
 *            click collapses.
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import useSWR from 'swr';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import ArenaCompetitions, { COMPETITIONS_KEY } from '@/components/ArenaCompetitions';
import type { CompetitionSummary } from '@/types/market';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;
const listMutate = jest.fn();

const comp = (over: Partial<CompetitionSummary> = {}): CompetitionSummary => ({
  id: 'c1',
  name: 'Friday Sprint',
  code: 'ABC234',
  status: 'running',
  member_count: 3,
  starts_at: '2026-07-12T00:00:00Z',
  ends_at: '2026-07-12T00:01:30Z',
  ...over,
});

function mockSWR(list: unknown, details: Record<string, unknown> = {}) {
  mockUseSWR.mockImplementation(((key: string) => {
    if (key === COMPETITIONS_KEY) return { data: list, mutate: listMutate };
    if (key in details) return { data: details[key], mutate: jest.fn() };
    return { data: undefined, mutate: jest.fn() };
  }) as never);
}

beforeEach(() => {
  jest.clearAllMocks();
  global.fetch = jest.fn();
  mockSWR({ competitions: [] });
});

describe('create form (comp-create)', () => {
  it('renders the create form, join controls, and the empty list state', () => {
    render(<ArenaCompetitions />);
    expect(screen.getByTestId('comp-create')).toBeInTheDocument();
    expect(screen.getByTestId('comp-name')).toBeInTheDocument();
    expect(screen.getByTestId('comp-hours')).toBeInTheDocument();
    expect(screen.getByTestId('comp-join-code')).toBeInTheDocument();
    expect(screen.getByTestId('comp-join')).toBeInTheDocument();
    expect(screen.getByTestId('comp-list').textContent).toContain('No competitions yet.');
    // nothing created yet → no code banner, no toast
    expect(screen.queryByTestId('comp-code')).toBeNull();
    expect(screen.queryByTestId('comp-toast')).toBeNull();
  });

  it('an empty name toasts the validation error without firing a request', async () => {
    render(<ArenaCompetitions />);
    await act(async () => {
      fireEvent.submit(screen.getByTestId('comp-create'));
    });
    expect(screen.getByTestId('comp-toast').textContent).toBe('Enter a name (1–40 characters).');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it.each(['0', '169', '1.5'])(
    'hours %j toasts the 1..168 validation error without firing a request',
    async (hours) => {
      render(<ArenaCompetitions />);
      fireEvent.change(screen.getByTestId('comp-name'), { target: { value: 'Friday Sprint' } });
      fireEvent.change(screen.getByTestId('comp-hours'), { target: { value: hours } });
      await act(async () => {
        fireEvent.submit(screen.getByTestId('comp-create'));
      });
      expect(screen.getByTestId('comp-toast').textContent).toBe(
        'Hours must be a whole number between 1 and 168.'
      );
      expect(global.fetch).not.toHaveBeenCalled();
    }
  );

  it('a successful create POSTs {name, hours}, shows the invite code, and revalidates the list', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ competition: comp({ code: 'QZ7K2M' }) }),
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-name'), { target: { value: '  Friday Sprint ' } });
    fireEvent.change(screen.getByTestId('comp-hours'), { target: { value: '48' } });
    await act(async () => {
      fireEvent.submit(screen.getByTestId('comp-create'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/competitions',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'Friday Sprint', hours: 48 }),
      })
    );
    expect(screen.getByTestId('comp-code').textContent).toBe('QZ7K2M');
    expect(screen.getByTestId('comp-copy')).toBeInTheDocument();
    expect(listMutate).toHaveBeenCalled();
    // the name clears for the next create; no error toast
    expect((screen.getByTestId('comp-name') as HTMLInputElement).value).toBe('');
    expect(screen.queryByTestId('comp-toast')).toBeNull();
  });

  it('a server error (e.g. the ≤5 running cap) surfaces in the toast', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Too many running competitions (max 5)' }),
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-name'), { target: { value: 'Friday Sprint' } });
    await act(async () => {
      fireEvent.submit(screen.getByTestId('comp-create'));
    });
    expect(screen.getByTestId('comp-toast').textContent).toBe(
      'Too many running competitions (max 5)'
    );
    expect(screen.queryByTestId('comp-code')).toBeNull();
  });

  it('the copy button writes the code to the clipboard and flips to Copied', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ competition: comp({ code: 'QZ7K2M' }) }),
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-name'), { target: { value: 'Friday Sprint' } });
    await act(async () => {
      fireEvent.submit(screen.getByTestId('comp-create'));
    });

    const button = screen.getByTestId('comp-copy');
    expect(button.textContent).toBe('Copy');
    await act(async () => {
      fireEvent.click(button);
    });
    expect(writeText).toHaveBeenCalledWith('QZ7K2M');
    expect(button.textContent).toBe('Copied');
  });
});

describe('join by invite code (comp-join)', () => {
  it('POSTs the trimmed uppercased code, clears the input, and revalidates', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-join-code'), { target: { value: ' qz7k2m ' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('comp-join'));
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/competitions/join',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ code: 'QZ7K2M' }),
      })
    );
    expect((screen.getByTestId('comp-join-code') as HTMLInputElement).value).toBe('');
    expect(listMutate).toHaveBeenCalled();
    expect(screen.queryByTestId('comp-toast')).toBeNull();
  });

  it('an unknown/ended code toasts the server error and keeps the input', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ error: 'Unknown invite code' }),
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-join-code'), { target: { value: 'NOPE99' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('comp-join'));
    });
    const toast = screen.getByTestId('comp-toast');
    expect(toast.textContent).toBe('Unknown invite code');
    expect(toast.style.border).toContain('rgb(239, 68, 68)'); // failure red, not direction vars
    expect((screen.getByTestId('comp-join-code') as HTMLInputElement).value).toBe('NOPE99');
  });

  it('a bodyless failure falls back to "Join failed (status)"', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => {
        throw new Error('no body');
      },
    });
    render(<ArenaCompetitions />);
    fireEvent.change(screen.getByTestId('comp-join-code'), { target: { value: 'ABC234' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('comp-join'));
    });
    expect(screen.getByTestId('comp-toast').textContent).toBe('Join failed (400)');
  });

  it('an empty code toasts locally without firing a request', async () => {
    render(<ArenaCompetitions />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('comp-join'));
    });
    expect(screen.getByTestId('comp-toast').textContent).toBe('Enter an invite code.');
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

describe('my competitions list + local countdown', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-07-12T00:00:00Z'));
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders comp-row-${id} with name, status chip, and member count', () => {
    mockSWR({
      competitions: [comp(), comp({ id: 'c2', name: 'Old Cup', status: 'ended', member_count: 5 })],
    });
    render(<ArenaCompetitions />);

    const row = screen.getByTestId('comp-row-c1');
    expect(row.textContent).toContain('Friday Sprint');
    expect(row.textContent).toContain('Running');
    expect(row.textContent).toContain('3 traders');

    const ended = screen.getByTestId('comp-row-c2');
    expect(ended.textContent).toContain('Old Cup');
    expect(ended.textContent).toContain('Ended');
    expect(ended.textContent).toContain('5 traders');
  });

  it('the countdown ticks down locally every second (comp-countdown-${id})', () => {
    mockSWR({ competitions: [comp()] }); // ends 90s after the pinned clock
    render(<ArenaCompetitions />);

    const countdown = screen.getByTestId('comp-countdown-c1');
    expect(countdown.textContent).toBe('0:01:30');
    act(() => {
      jest.advanceTimersByTime(1000);
    });
    expect(countdown.textContent).toBe('0:01:29');
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(countdown.textContent).toBe('0:01:27');
  });

  it('a running countdown clamps at 0:00:00 once past ends_at; ended rows show —', () => {
    mockSWR({
      competitions: [
        comp({ id: 'c1', ends_at: '2026-07-12T00:00:02Z' }),
        comp({ id: 'c2', status: 'ended' }),
      ],
    });
    render(<ArenaCompetitions />);
    expect(screen.getByTestId('comp-countdown-c2').textContent).toBe('—');
    act(() => {
      jest.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId('comp-countdown-c1').textContent).toBe('0:00:00');
  });

  it('unmount clears the shared 1s interval (no timer leak)', () => {
    mockSWR({ competitions: [comp()] });
    const { unmount } = render(<ArenaCompetitions />);
    expect(jest.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(jest.getTimerCount()).toBe(0);
  });
});

describe('expanded board (comp-board-${id})', () => {
  const detail = (status: string) => ({
    ...comp({ status: status as CompetitionSummary['status'] }),
    board: [
      { user_id: 'u1', name: 'Ada', baseline_value: 10000, value: 10850.25, return_pct: 8.5, rank: 1 },
      { user_id: 'u2', name: 'Bob', baseline_value: 10000, value: 9700, return_pct: -3, rank: 2 },
      { user_id: 'u3', name: 'Cyd', baseline_value: 10000, value: 10000, return_pct: 0, rank: 3 },
    ],
  });

  it('clicking a row expands the board with rank/name/value and direction-coloured return%', () => {
    mockSWR({ competitions: [comp()] }, { '/api/competitions/c1': detail('running') });
    render(<ArenaCompetitions />);

    expect(screen.queryByTestId('comp-board-c1')).toBeNull();
    fireEvent.click(screen.getByTestId('comp-row-toggle-c1'));
    expect(screen.getByTestId('comp-board-c1')).toBeInTheDocument();

    const first = screen.getByTestId('comp-board-c1-rank-1');
    expect(first.textContent).toContain('Ada');
    expect(first.textContent).toContain('$10,850.25'); // formatMoney
    expect(first.textContent).toContain('+8.50%');
    expect(first.querySelector('.text-terminal-up')).toBeTruthy();

    const second = screen.getByTestId('comp-board-c1-rank-2');
    expect(second.textContent).toContain('-3.00%');
    expect(second.querySelector('.text-terminal-down')).toBeTruthy();

    const third = screen.getByTestId('comp-board-c1-rank-3');
    expect(third.textContent).toContain('0.00%');
    expect(third.querySelector('.text-terminal-up')).toBeNull();
    expect(third.querySelector('.text-terminal-down')).toBeNull();

    // running board — no final-standings marker
    expect(screen.queryByTestId('comp-final-c1')).toBeNull();
  });

  it('an ended competition board carries the final-standings marker', () => {
    mockSWR(
      { competitions: [comp({ status: 'ended' })] },
      { '/api/competitions/c1': detail('ended') }
    );
    render(<ArenaCompetitions />);
    fireEvent.click(screen.getByTestId('comp-row-toggle-c1'));
    expect(screen.getByTestId('comp-final-c1').textContent).toBe('Final standings');
  });

  it('clicking the row again collapses the board', () => {
    mockSWR({ competitions: [comp()] }, { '/api/competitions/c1': detail('running') });
    render(<ArenaCompetitions />);
    fireEvent.click(screen.getByTestId('comp-row-toggle-c1'));
    expect(screen.getByTestId('comp-board-c1')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('comp-row-toggle-c1'));
    expect(screen.queryByTestId('comp-board-c1')).toBeNull();
  });
});
