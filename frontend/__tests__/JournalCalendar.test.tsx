/**
 * JournalCalendar.test.tsx — /journal realized-P&L month calendar (P4 §3).
 *
 * Pure helpers: monthGrid (leap Feb, week start 0/1, 7-col padding),
 *               shiftMonth rollover, dailyRealized aggregation, pnlIntensity
 *               relative-to-month-max mapping, weekdayHeaders.
 * Rendering:    day cells with intensity/direction attributes + formatted
 *               realized P&L, prev/next month navigation, click-to-filter on
 *               traded days (and only those), selected-day clear chip.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import useSWR from 'swr';
import type { TradeRecord } from '@/types/market';

jest.mock('swr', () => ({
  __esModule: true,
  default: jest.fn(),
}));

import JournalCalendar, {
  monthGrid,
  monthOf,
  shiftMonth,
  dailyRealized,
  pnlIntensity,
  weekdayHeaders,
} from '@/components/JournalCalendar';

const mockUseSWR = useSWR as jest.MockedFunction<typeof useSWR>;

// Timezone-naive ISO strings parse as LOCAL time — deterministic in any TZ.
const trade = (over: Partial<TradeRecord>): TradeRecord => ({
  id: 'tr-x',
  ticker: 'AAPL',
  side: 'buy',
  quantity: 5,
  price: 100,
  executed_at: '2026-07-07T10:00:00',
  commission: 0,
  realized_pnl: null,
  ...over,
});

describe('calendar helpers (P4 §3)', () => {
  it('monthGrid: leap February 2024 has 29 days and a 7-multiple length', () => {
    const cells = monthGrid({ year: 2024, month: 2 }, 0);
    expect(cells.length % 7).toBe(0);
    expect(cells).toContain('2024-02-29');
    // 2024-02-01 is a Thursday → 4 leading nulls with a Sunday week start
    expect(cells.slice(0, 4)).toEqual([null, null, null, null]);
    expect(cells[4]).toBe('2024-02-01');
  });

  it('monthGrid: non-leap February has exactly 28 day cells', () => {
    const cells = monthGrid({ year: 2026, month: 2 }, 0);
    expect(cells.filter((c) => c !== null)).toHaveLength(28);
    expect(cells).not.toContain('2026-02-29');
  });

  it('monthGrid honours the Monday week start (CN)', () => {
    // 2024-02-01 (Thursday): Monday start → 3 leading nulls
    const cells = monthGrid({ year: 2024, month: 2 }, 1);
    expect(cells.slice(0, 3)).toEqual([null, null, null]);
    expect(cells[3]).toBe('2024-02-01');
    expect(cells.length % 7).toBe(0);
  });

  it('shiftMonth rolls over year boundaries in both directions', () => {
    expect(shiftMonth({ year: 2026, month: 1 }, -1)).toEqual({ year: 2025, month: 12 });
    expect(shiftMonth({ year: 2026, month: 12 }, 1)).toEqual({ year: 2027, month: 1 });
    expect(shiftMonth({ year: 2026, month: 7 }, 1)).toEqual({ year: 2026, month: 8 });
  });

  it('monthOf reads the month from a Date', () => {
    expect(monthOf(new Date(2026, 6, 10))).toEqual({ year: 2026, month: 7 });
  });

  it('dailyRealized aggregates Σ realized_pnl and counts per local day (buys count 0)', () => {
    const byDay = dailyRealized([
      trade({ id: 'a', executed_at: '2026-07-07T15:00:00', side: 'sell', realized_pnl: 30 }),
      trade({ id: 'b', executed_at: '2026-07-07T09:00:00', realized_pnl: null }),
      trade({ id: 'c', executed_at: '2026-07-06T12:00:00', side: 'sell', realized_pnl: -12.5 }),
      trade({ id: 'bad', executed_at: 'not-a-date' }),
    ]);
    expect(byDay['2026-07-07']).toEqual({ realized: 30, count: 2 });
    expect(byDay['2026-07-06']).toEqual({ realized: -12.5, count: 1 });
    expect(Object.keys(byDay)).toHaveLength(2);
  });

  it('pnlIntensity maps |pnl| relative to the month max, clamped 0..100', () => {
    expect(pnlIntensity(100, 100)).toBe(100);
    expect(pnlIntensity(-50, 100)).toBe(50);
    expect(pnlIntensity(150, 100)).toBe(100); // clamp
    expect(pnlIntensity(0, 100)).toBe(0); // no realized → transparent
    expect(pnlIntensity(50, 0)).toBe(0); // no scale
    expect(pnlIntensity(NaN, 100)).toBe(0);
  });

  it('weekdayHeaders returns 7 locale abbreviations honouring the week start', () => {
    const us = weekdayHeaders('en-US', 0);
    expect(us).toHaveLength(7);
    expect(us[0]).toBe('Sun');
    expect(us[6]).toBe('Sat');
    const mondayFirst = weekdayHeaders('en-US', 1);
    expect(mondayFirst[0]).toBe('Mon');
    expect(mondayFirst[6]).toBe('Sun');
  });
});

describe('JournalCalendar (P4 §3)', () => {
  const JULY = { year: 2026, month: 7 };
  const trades = [
    trade({ id: 'a', executed_at: '2026-07-07T15:00:00', side: 'sell', realized_pnl: 30 }),
    trade({ id: 'b', executed_at: '2026-07-06T12:00:00', side: 'sell', realized_pnl: -15 }),
    trade({ id: 'c', executed_at: '2026-07-06T13:00:00', realized_pnl: null }),
  ];

  beforeEach(() => {
    jest.clearAllMocks();
    // profile undefined → US defaults (en-US, $, Sunday week start)
    mockUseSWR.mockReturnValue({ data: undefined, mutate: jest.fn() } as never);
  });

  it('renders traded day cells with direction, month-relative intensity, and formatted P&L', () => {
    render(
      <JournalCalendar trades={trades} selectedDay={null} onSelectDay={jest.fn()} initialMonth={JULY} />
    );
    expect(screen.getByTestId('journal-calendar')).toBeTruthy();

    const win = screen.getByTestId('journal-cal-day-2026-07-07');
    expect(win.getAttribute('data-direction')).toBe('up');
    expect(win.getAttribute('data-intensity')).toBe('100'); // |30| is the month max
    expect(win.textContent).toContain('+$30.00');

    const loss = screen.getByTestId('journal-cal-day-2026-07-06');
    expect(loss.getAttribute('data-direction')).toBe('down');
    expect(loss.getAttribute('data-intensity')).toBe('50'); // |−15| / 30
    expect(loss.getAttribute('data-trades')).toBe('2');
    expect(loss.textContent).toContain('-$15.00');

    // a day with no trades stays transparent and unclickable
    const idle = screen.getByTestId('journal-cal-day-2026-07-01');
    expect(idle.getAttribute('data-intensity')).toBe('0');
    expect(idle.getAttribute('data-trades')).toBe('0');
    expect((idle as HTMLButtonElement).disabled).toBe(true);
  });

  it('prev/next navigate months and update the locale month title', () => {
    render(
      <JournalCalendar trades={trades} selectedDay={null} onSelectDay={jest.fn()} initialMonth={JULY} />
    );
    expect(screen.getByTestId('journal-cal-title').textContent).toBe('July 2026');

    fireEvent.click(screen.getByTestId('journal-cal-prev'));
    expect(screen.getByTestId('journal-cal-title').textContent).toBe('June 2026');
    expect(screen.queryByTestId('journal-cal-day-2026-07-07')).toBeNull();
    expect(screen.getByTestId('journal-cal-day-2026-06-15')).toBeTruthy();

    fireEvent.click(screen.getByTestId('journal-cal-next'));
    fireEvent.click(screen.getByTestId('journal-cal-next'));
    expect(screen.getByTestId('journal-cal-title').textContent).toBe('August 2026');
  });

  it('clicking a traded day selects it; clicking it again clears; idle days never fire', () => {
    const onSelectDay = jest.fn();
    const { rerender } = render(
      <JournalCalendar trades={trades} selectedDay={null} onSelectDay={onSelectDay} initialMonth={JULY} />
    );

    fireEvent.click(screen.getByTestId('journal-cal-day-2026-07-07'));
    expect(onSelectDay).toHaveBeenCalledWith('2026-07-07');

    fireEvent.click(screen.getByTestId('journal-cal-day-2026-07-01')); // no trades
    expect(onSelectDay).toHaveBeenCalledTimes(1);

    rerender(
      <JournalCalendar
        trades={trades}
        selectedDay="2026-07-07"
        onSelectDay={onSelectDay}
        initialMonth={JULY}
      />
    );
    fireEvent.click(screen.getByTestId('journal-cal-day-2026-07-07'));
    expect(onSelectDay).toHaveBeenLastCalledWith(null);
  });

  it('shows a clear chip while a day filter is active', () => {
    const onSelectDay = jest.fn();
    render(
      <JournalCalendar
        trades={trades}
        selectedDay="2026-07-06"
        onSelectDay={onSelectDay}
        initialMonth={JULY}
      />
    );
    const chip = screen.getByTestId('journal-cal-clear');
    expect(chip.textContent).toContain('2026-07-06');
    fireEvent.click(chip);
    expect(onSelectDay).toHaveBeenCalledWith(null);
  });

  it('cn: renders ¥ amounts, a Chinese month title, and Monday-first weekday headers', () => {
    mockUseSWR.mockReturnValue({
      data: { market: 'cn', currency_symbol: '¥', locale: 'zh-CN', up_is_red: true },
      mutate: jest.fn(),
    } as never);
    render(
      <JournalCalendar trades={trades} selectedDay={null} onSelectDay={jest.fn()} initialMonth={JULY} />
    );
    expect(screen.getByTestId('journal-cal-title').textContent).toBe(
      new Date(2026, 6, 1).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long' })
    );
    expect(screen.getByTestId('journal-cal-day-2026-07-07').textContent).toContain('+¥30.00');
    // Monday week start: 2026-07-01 (Wednesday) sits at grid index 2
    const grid = monthGrid(JULY, 1);
    expect(grid[2]).toBe('2026-07-01');
  });
});
