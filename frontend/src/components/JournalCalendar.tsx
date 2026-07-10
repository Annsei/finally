/**
 * JournalCalendar.tsx — /journal monthly realized-P&L calendar (P4 §3).
 *
 * Pure frontend: reuses the trades the page already fetched (500) and
 * aggregates realized_pnl per LOCAL day. Day cells are tinted with the
 * direction colour variables via color-mix at an intensity of |day pnl|
 * relative to the month's max |pnl| (0-trade days stay transparent); today is
 * outlined in accent. Clicking a day that HAS trades hands the day to the
 * page, which narrows the by-day section to it (click again to clear).
 *
 * Pure helpers (monthGrid / dailyRealized / pnlIntensity / shiftMonth /
 * weekdayHeaders) are exported for direct jest coverage.
 */
import { useMemo, useState } from 'react';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import type { TradeRecord } from '@/types/market';

export interface CalendarMonth {
  year: number;
  month: number; // 1-12
}

/** The month containing `now` (defaults to the wall clock). */
export function monthOf(now: Date = new Date()): CalendarMonth {
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

/** Shift a month by ±delta, rolling over year boundaries. */
export function shiftMonth(m: CalendarMonth, delta: number): CalendarMonth {
  const d = new Date(m.year, m.month - 1 + delta, 1);
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

/**
 * 7-column month grid: leading/trailing `null` padding around YYYY-MM-DD day
 * keys. `weekStartsOn` 0 = Sunday (US), 1 = Monday (CN). Length is always a
 * multiple of 7.
 */
export function monthGrid(m: CalendarMonth, weekStartsOn: 0 | 1 = 0): (string | null)[] {
  const first = new Date(m.year, m.month - 1, 1);
  const daysInMonth = new Date(m.year, m.month, 0).getDate();
  const lead = (first.getDay() - weekStartsOn + 7) % 7;
  const cells: (string | null)[] = new Array<string | null>(lead).fill(null);
  const mm = String(m.month).padStart(2, '0');
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(`${m.year}-${mm}-${String(d).padStart(2, '0')}`);
  }
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

/** Local calendar day (YYYY-MM-DD) of an ISO timestamp; null if unparseable. */
function calDayOf(iso: string): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export interface DayPnl {
  realized: number; // Σ realized_pnl (buys count 0)
  count: number; // trade count
}

/** Aggregate Σ realized_pnl + trade count per local day. */
export function dailyRealized(trades: TradeRecord[]): Record<string, DayPnl> {
  const out: Record<string, DayPnl> = {};
  for (const trade of trades) {
    const day = calDayOf(trade.executed_at);
    if (day === null) continue;
    const entry = out[day] ?? (out[day] = { realized: 0, count: 0 });
    entry.realized += trade.realized_pnl ?? 0;
    entry.count += 1;
  }
  return out;
}

/**
 * color-mix intensity for a day cell: |pnl| relative to the month's max
 * |pnl|, rounded to 0..100 and clamped. 0 pnl (or no scale) → 0.
 */
export function pnlIntensity(pnl: number, maxAbs: number): number {
  if (!Number.isFinite(pnl) || pnl === 0) return 0;
  if (!Number.isFinite(maxAbs) || maxAbs <= 0) return 0;
  return Math.round(Math.min(Math.abs(pnl) / maxAbs, 1) * 100);
}

/** Localized weekday header abbreviations, honouring the week start. */
export function weekdayHeaders(locale: string, weekStartsOn: 0 | 1): string[] {
  // 1970-01-04 was a Sunday — offset from it for a stable weekday sequence.
  return Array.from({ length: 7 }, (_, i) =>
    new Date(1970, 0, 4 + weekStartsOn + i).toLocaleDateString(locale, { weekday: 'short' })
  );
}

export default function JournalCalendar({
  trades,
  selectedDay,
  onSelectDay,
  initialMonth,
}: {
  trades: TradeRecord[];
  selectedDay: string | null;
  onSelectDay: (day: string | null) => void;
  initialMonth?: CalendarMonth; // test hook — defaults to the current month
}) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const weekStartsOn: 0 | 1 = profile.locale.toLowerCase().startsWith('zh') ? 1 : 0;

  const [month, setMonth] = useState<CalendarMonth>(() => initialMonth ?? monthOf());

  const byDay = useMemo(() => dailyRealized(trades), [trades]);
  const cells = useMemo(() => monthGrid(month, weekStartsOn), [month, weekStartsOn]);
  const monthMaxAbs = useMemo(
    () =>
      cells.reduce(
        (max, day) => (day !== null ? Math.max(max, Math.abs(byDay[day]?.realized ?? 0)) : max),
        0
      ),
    [cells, byDay]
  );

  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
    now.getDate()
  ).padStart(2, '0')}`;
  const monthTitle = new Date(month.year, month.month - 1, 1).toLocaleDateString(profile.locale, {
    year: 'numeric',
    month: 'long',
  });

  return (
    <div data-testid="journal-calendar" className="p-2">
      {/* Month navigation */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <button
          type="button"
          data-testid="journal-cal-prev"
          aria-label={t('journal.calPrevAria')}
          onClick={() => setMonth((m) => shiftMonth(m, -1))}
          className="px-1.5 py-0.5 rounded text-xs text-terminal-muted hover:text-terminal-text border border-terminal-border"
        >
          ‹
        </button>
        <span
          data-testid="journal-cal-title"
          className="text-xs font-semibold text-terminal-text tabular-nums"
        >
          {monthTitle}
        </span>
        <button
          type="button"
          data-testid="journal-cal-next"
          aria-label={t('journal.calNextAria')}
          onClick={() => setMonth((m) => shiftMonth(m, 1))}
          className="px-1.5 py-0.5 rounded text-xs text-terminal-muted hover:text-terminal-text border border-terminal-border"
        >
          ›
        </button>
      </div>

      {/* Active day filter chip */}
      {selectedDay !== null && (
        <button
          type="button"
          data-testid="journal-cal-clear"
          onClick={() => onSelectDay(null)}
          className="mb-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border border-terminal-accent text-terminal-accent"
        >
          {selectedDay} ✕ {t('journal.calClear')}
        </button>
      )}

      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-px mb-px">
        {weekdayHeaders(profile.locale, weekStartsOn).map((wd, i) => (
          <span
            key={`${wd}-${i}`}
            className="text-center text-[9px] text-terminal-muted uppercase tracking-wide"
          >
            {wd}
          </span>
        ))}
      </div>

      {/* Day cells */}
      <div className="grid grid-cols-7 gap-px">
        {cells.map((day, i) => {
          if (day === null) {
            return <span key={`pad-${i}`} className="h-9" />;
          }
          const entry = byDay[day];
          const realized = entry?.realized ?? 0;
          const hasTrades = (entry?.count ?? 0) > 0;
          const intensity = pnlIntensity(realized, monthMaxAbs);
          const colorVar = realized >= 0 ? 'var(--color-up)' : 'var(--color-down)';
          const isToday = day === today;
          const isSelected = day === selectedDay;
          const realizedColor =
            realized === 0
              ? 'text-terminal-muted'
              : realized > 0
                ? 'text-terminal-up'
                : 'text-terminal-down';
          return (
            <button
              type="button"
              key={day}
              data-testid={`journal-cal-day-${day}`}
              data-intensity={intensity}
              data-direction={realized === 0 ? 'flat' : realized > 0 ? 'up' : 'down'}
              data-trades={entry?.count ?? 0}
              disabled={!hasTrades}
              onClick={() => onSelectDay(isSelected ? null : day)}
              className={`h-9 rounded-[2px] px-0.5 text-left align-top border ${
                isToday
                  ? 'border-terminal-accent'
                  : isSelected
                    ? 'border-terminal-blue'
                    : 'border-transparent'
              } ${hasTrades ? 'cursor-pointer hover:border-terminal-border' : 'cursor-default'}`}
              style={{
                background:
                  intensity > 0
                    ? `color-mix(in srgb, ${colorVar} ${intensity}%, transparent)`
                    : 'transparent',
              }}
            >
              <span className="block text-[9px] leading-3 text-terminal-muted tabular-nums">
                {Number(day.slice(8))}
              </span>
              {hasTrades && (
                <span className={`block text-[8px] leading-3 tabular-nums truncate ${realizedColor}`}>
                  {`${realized >= 0 ? '+' : '-'}${formatMoney(Math.abs(realized), money)}`}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
