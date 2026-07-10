/**
 * journal.tsx — /journal trade-review page (P1 §6). Exported statically as
 * journal/index.html.
 *
 * Left: trades grouped by local day (journal-days) with per-day realized P&L
 * totals and a client-side ticker filter (journal-filter).
 * Right: the review archive (journal-reviews) fed by
 * GET /api/chat/?kind=review&limit=100, plus a "run review" button that POSTs
 * /api/chat/review and revalidates the list (same loading/error pattern as
 * ChatPanel's review button).
 *
 * P4 §3 (additive): journal-calendar — monthly realized-P&L calendar above
 * the review archive, reusing the same fetched trades. Clicking a day that
 * has trades narrows the by-day section to that day (dayFilter below).
 */
import { useMemo, useState } from 'react';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import { KIND_BORDER } from '@/components/ChatPanel';
import JournalCalendar from '@/components/JournalCalendar';
import SymbolLink from '@/components/SymbolLink';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatMoney, formatShares } from '@/lib/format';
import type { ChatHistoryResponse, TradeRecord, TradesResponse } from '@/types/market';

const REVIEWS_KEY = '/api/chat/?kind=review&limit=100';
const TRADES_KEY = '/api/portfolio/trades?limit=500';

export interface DayGroup {
  day: string; // local YYYY-MM-DD
  trades: TradeRecord[];
  realized: number; // Σ realized_pnl (sells; buys count as 0)
  count: number;
}

/** Local calendar day (YYYY-MM-DD) of an ISO timestamp. */
export function localDayOf(iso: string): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/**
 * Group trades by local day, newest day first (P1 §6). Trade order within a
 * day is preserved from the input (the API returns newest first). Each group
 * carries the day's realized-P&L total.
 */
export function groupTradesByDay(trades: TradeRecord[]): DayGroup[] {
  const byDay = new Map<string, DayGroup>();
  for (const trade of trades) {
    const day = localDayOf(trade.executed_at);
    if (day === null) continue;
    let group = byDay.get(day);
    if (!group) {
      group = { day, trades: [], realized: 0, count: 0 };
      byDay.set(day, group);
    }
    group.trades.push(trade);
    group.realized += trade.realized_pnl ?? 0;
    group.count += 1;
  }
  return [...byDay.values()].sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : 0));
}

function formatTradeTime(iso: string, locale: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString(locale, { hour12: false });
}

/**
 * Localized day heading for a YYYY-MM-DD group key. Parsed component-wise so
 * the LOCAL day is preserved (new Date('YYYY-MM-DD') would parse as UTC and
 * can shift a day in negative-offset timezones).
 */
function formatDayHeading(day: string, locale: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const parsed = new Date(y, (m ?? 1) - 1, d ?? 1);
  return isNaN(parsed.getTime())
    ? day
    : parsed.toLocaleDateString(locale, { year: 'numeric', month: 'long', day: 'numeric' });
}

function formatReviewTime(iso: string, locale: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(locale, { hour12: false });
}

export default function JournalPage() {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };

  // ---- Review archive (right column) ----
  const { data: reviewsData, mutate: mutateReviews } = useSWR<ChatHistoryResponse>(
    REVIEWS_KEY,
    fetcher
  );
  // The endpoint returns the most recent N ascending — show newest first.
  const reviews = useMemo(
    () => [...(reviewsData?.messages ?? [])].reverse(),
    [reviewsData]
  );
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runReview = async () => {
    if (running) return;
    setRunning(true);
    setError(null);
    try {
      const res = await fetch('/api/chat/review', { method: 'POST' });
      if (!res.ok) {
        let detail = '';
        try {
          const body = await res.json();
          detail = body?.error ?? body?.detail ?? '';
        } catch {
          // Non-JSON error body — fall through to the generic message.
        }
        throw new Error(detail || `${t('journal.reviewFailed')} (${res.status})`);
      }
      await mutateReviews();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t('journal.reviewFailed'));
    } finally {
      setRunning(false);
    }
  };

  // ---- Trades by day (left column) ----
  const { data: tradesData } = useSWR<TradesResponse>(TRADES_KEY, fetcher);
  const [filter, setFilter] = useState('');
  // P4 §3: day filter set by clicking a traded day in the calendar. null (the
  // default) leaves the by-day section exactly as before.
  const [dayFilter, setDayFilter] = useState<string | null>(null);
  const days = useMemo(() => {
    const all = tradesData?.trades ?? [];
    const needle = filter.trim().toUpperCase();
    const filtered = needle === '' ? all : all.filter((tr) => tr.ticker.includes(needle));
    const groups = groupTradesByDay(filtered);
    return dayFilter === null ? groups : groups.filter((g) => g.day === dayFilter);
  }, [tradesData, filter, dayFilter]);

  const sectionTitleClass =
    'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0 flex items-center justify-between gap-2';

  return (
    <AppShell>
      <div className="flex gap-4 h-full min-h-0">
        {/* Trades grouped by local day */}
        <section className="flex-[3] min-w-0 flex flex-col min-h-0 border border-terminal-border rounded bg-terminal-surface/30">
          <div className={sectionTitleClass}>
            <h2>{t('journal.daysTitle')}</h2>
            <input
              type="text"
              data-testid="journal-filter"
              aria-label={t('journal.filterAria')}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t('journal.filterPlaceholder')}
              className="w-40 px-2 py-0.5 text-xs font-mono normal-case tracking-normal bg-terminal-bg border border-terminal-border text-terminal-text rounded placeholder-terminal-muted focus:outline-none focus:border-terminal-blue"
            />
          </div>
          <div data-testid="journal-days" className="flex-1 min-h-0 overflow-auto p-2">
            {!tradesData ? (
              // Request in flight — data-gated like the reviews column, so the
              // empty state never flashes while trades are still loading.
              <p className="text-xs text-terminal-muted">{t('journal.daysLoading')}</p>
            ) : days.length === 0 ? (
              <p className="text-xs text-terminal-muted">{t('journal.daysEmpty')}</p>
            ) : (
              days.map((group) => {
                const realizedColor =
                  group.realized === 0
                    ? 'text-terminal-muted'
                    : group.realized > 0
                      ? 'text-terminal-up'
                      : 'text-terminal-down';
                return (
                  <div
                    key={group.day}
                    data-testid={`journal-day-${group.day}`}
                    className="mb-3"
                  >
                    <div className="flex items-baseline gap-3 border-b border-terminal-border pb-1 mb-1">
                      <span className="text-xs font-semibold text-terminal-text tabular-nums">
                        {formatDayHeading(group.day, profile.locale)}
                      </span>
                      <span className="text-[10px] text-terminal-muted">
                        {t('journal.tradeCount', { n: group.count })}
                      </span>
                      <span
                        className={`ml-auto text-xs tabular-nums ${realizedColor}`}
                        data-testid={`journal-realized-${group.day}`}
                      >
                        {t('journal.dayRealized')}{' '}
                        {`${group.realized >= 0 ? '+' : '-'}${formatMoney(
                          Math.abs(group.realized),
                          money
                        )}`}
                      </span>
                    </div>
                    <table className="w-full text-xs border-collapse">
                      <tbody>
                        {group.trades.map((trade) => (
                          <tr
                            key={trade.id}
                            data-testid={`journal-trade-${trade.id}`}
                            className="border-b border-terminal-border/40"
                          >
                            <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                              {formatTradeTime(trade.executed_at, profile.locale)}
                            </td>
                            <td className="py-1 font-semibold text-terminal-text">
                              <SymbolLink code={trade.ticker} />
                            </td>
                            <td
                              className={`py-1 font-semibold ${
                                trade.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                              }`}
                            >
                              {t(trade.side === 'buy' ? 'tradebar.buy' : 'tradebar.sell')}
                            </td>
                            <td className="text-right py-1 tabular-nums text-terminal-text">
                              {formatShares(trade.quantity, profile)}
                            </td>
                            <td className="text-right py-1 tabular-nums text-terminal-text">
                              {formatMoney(trade.price, money)}
                            </td>
                            <td className="text-right py-1 tabular-nums text-terminal-muted">
                              {trade.commission ? formatMoney(trade.commission, money) : '—'}
                            </td>
                            <td
                              className={`text-right py-1 pr-1 tabular-nums ${
                                trade.realized_pnl == null
                                  ? 'text-terminal-muted'
                                  : trade.realized_pnl >= 0
                                    ? 'text-terminal-up'
                                    : 'text-terminal-down'
                              }`}
                            >
                              {trade.realized_pnl != null
                                ? `${trade.realized_pnl >= 0 ? '+' : '-'}${formatMoney(
                                    Math.abs(trade.realized_pnl),
                                    money
                                  )}`
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              })
            )}
          </div>
        </section>

        {/* Right column: P&L calendar (P4 §3, additive) + review archive */}
        <div className="flex-[2] min-w-0 flex flex-col gap-4 min-h-0">
        <section className="shrink-0 border border-terminal-border rounded bg-terminal-surface/30 flex flex-col">
          <h2 className="px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0">
            {t('journal.calTitle')}
          </h2>
          <JournalCalendar
            trades={tradesData?.trades ?? []}
            selectedDay={dayFilter}
            onSelectDay={setDayFilter}
          />
        </section>

        {/* Review archive */}
        <section className="flex-1 min-w-0 flex flex-col min-h-0 border border-terminal-border rounded bg-terminal-surface/30">
          <div className={sectionTitleClass}>
            <h2>{t('journal.reviewsTitle')}</h2>
            <button
              type="button"
              data-testid="journal-run-review"
              onClick={() => void runReview()}
              disabled={running}
              className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider text-white disabled:opacity-50"
              style={{ backgroundColor: '#753991' }}
            >
              {running ? t('journal.running') : t('journal.runReview')}
            </button>
          </div>
          <div data-testid="journal-reviews" className="flex-1 min-h-0 overflow-auto p-2">
            {error && (
              <p
                data-testid="journal-review-error"
                className="mb-2 text-xs text-terminal-down leading-relaxed"
              >
                {error}
              </p>
            )}
            {reviewsData && reviews.length === 0 && !error ? (
              <p className="text-xs text-terminal-muted leading-relaxed">
                {t('journal.reviewsEmpty')}
              </p>
            ) : (
              reviews.map((msg, idx) => (
                <div
                  key={`${msg.created_at}-${idx}`}
                  data-testid={`journal-review-${idx}`}
                  className="mb-3 bg-terminal-surface rounded px-3 py-2"
                  style={{ borderLeft: `2px solid ${KIND_BORDER.review}` }}
                >
                  <div className="flex items-baseline justify-between gap-2 mb-0.5">
                    <span
                      className="text-[10px] font-semibold uppercase tracking-wider"
                      style={{ color: KIND_BORDER.review }}
                    >
                      {t('chat.kind.review')}
                    </span>
                    <span className="text-[10px] text-terminal-muted tabular-nums">
                      {formatReviewTime(msg.created_at, profile.locale)}
                    </span>
                  </div>
                  <p className="text-xs text-terminal-text leading-relaxed whitespace-pre-wrap">
                    {msg.content}
                  </p>
                </div>
              ))
            )}
          </div>
        </section>
        </div>
      </div>
    </AppShell>
  );
}
