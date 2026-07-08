/**
 * EventArchive.tsx — paged market-event archive list (P1 §4/§5).
 *
 * Reads GET /api/market/events/archive (the durable SQLite archive, P1 §3.3)
 * — unlike the NewsTicker's /api/market/events ring buffer this survives
 * restarts and paginates via the `before` timestamp cursor.
 *
 * Testid namespace is parameterized so both pages keep their contract ids:
 *   /market → market-events, market-event-${id}, market-events-more
 *   /symbol → symbol-events, symbol-event-${id}, symbol-events-more
 *
 * Direction colour goes through var(--color-up/down) only, so the A-share
 * red-up/green-down flip engages at the CSS-variable layer (P1 §8).
 */
import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import SymbolLink from '@/components/SymbolLink';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import type { MarketEvent, MarketEventsArchiveResponse } from '@/types/market';

interface Props {
  prefix: 'market' | 'symbol';
  ticker?: string; // optional exact-match filter (symbol page)
  emptyKey: string; // i18n key for the empty state
  pageSize?: number;
}

function formatEventTime(ts: number, locale: string): string {
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? '' : d.toLocaleString(locale, { hour12: false });
}

export default function EventArchive({ prefix, ticker, emptyKey, pageSize = 50 }: Props) {
  const t = useT();
  const profile = useMarketProfile();
  const key = `/api/market/events/archive?limit=${pageSize}${
    ticker ? `&ticker=${encodeURIComponent(ticker)}` : ''
  }`;
  const { data } = useSWR<MarketEventsArchiveResponse>(key, fetcher);

  // Older pages accumulate locally; the SWR first page stays authoritative for
  // the newest window. Dedupe by id in case a revalidated first page slides.
  const [older, setOlder] = useState<MarketEvent[]>([]);
  const [olderHasMore, setOlderHasMore] = useState<boolean | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const firstPage = data?.events ?? [];

  // Once "load more" is in use, the list is an accumulated archive — when a
  // revalidated first page slides forward (new events arrive), events that
  // fell out of the newest window would silently vanish between the first
  // page and `older`. Fold them into `older` instead (merge by id, dedupe,
  // newest first). With no pagination in play, `older` stays empty and the
  // pure newest-window semantics are unchanged.
  const prevFirstPageRef = useRef<MarketEvent[]>([]);
  useEffect(() => {
    const prev = prevFirstPageRef.current;
    const current = data?.events ?? [];
    prevFirstPageRef.current = current;
    if (prev.length === 0) return;
    const currentIds = new Set(current.map((e) => e.id));
    const slidOut = prev.filter((e) => !currentIds.has(e.id));
    if (slidOut.length === 0) return;
    setOlder((prevOlder) => {
      if (prevOlder.length === 0) return prevOlder;
      const olderIds = new Set(prevOlder.map((e) => e.id));
      const additions = slidOut.filter((e) => !olderIds.has(e.id));
      if (additions.length === 0) return prevOlder;
      return [...prevOlder, ...additions].sort((a, b) => b.timestamp - a.timestamp);
    });
  }, [data]);

  const seen = new Set(firstPage.map((e) => e.id));
  const events = [...firstPage, ...older.filter((e) => !seen.has(e.id))];
  const hasMore = olderHasMore ?? data?.has_more ?? false;

  const loadMore = async () => {
    if (loadingMore || events.length === 0) return;
    setLoadingMore(true);
    try {
      // Cursor = oldest timestamp across the merged list, so paging continues
      // past everything already shown even after first-page/older merges.
      const before = Math.min(...events.map((e) => e.timestamp));
      const page: MarketEventsArchiveResponse = await fetcher(`${key}&before=${before}`);
      setOlder((prev) => [...prev, ...page.events]);
      setOlderHasMore(page.has_more);
    } catch {
      // Keep the button enabled — the user can retry.
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div data-testid={`${prefix}-events`} className="flex flex-col">
      {data && events.length === 0 && (
        <p className="text-xs text-terminal-muted leading-relaxed">{t(emptyKey)}</p>
      )}
      {events.map((ev) => {
        const color = ev.direction === 'up' ? 'var(--color-up)' : 'var(--color-down)';
        return (
          <div
            key={ev.id}
            data-testid={`${prefix}-event-${ev.id}`}
            className="border-b border-terminal-border/60 py-1.5 text-xs"
          >
            <div className="flex items-baseline gap-2">
              <span className="text-terminal-muted tabular-nums shrink-0">
                {formatEventTime(ev.timestamp, profile.locale)}
              </span>
              <SymbolLink code={ev.ticker} className="font-semibold text-terminal-text shrink-0" />
              <span className="text-terminal-text flex-1 min-w-0 truncate">{ev.headline}</span>
              <span className="tabular-nums shrink-0" style={{ color }}>
                {ev.change_percent > 0 ? '+' : ''}
                {ev.change_percent.toFixed(2)}%
              </span>
            </div>
            {ev.narrative && (
              <p className="mt-0.5 text-terminal-muted leading-snug">{ev.narrative}</p>
            )}
          </div>
        );
      })}
      {hasMore && events.length > 0 && (
        <button
          type="button"
          data-testid={`${prefix}-events-more`}
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="mt-2 self-start text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-accent disabled:opacity-50 transition-colors"
        >
          {loadingMore ? t('market.loadingMore') : t('market.loadMore')}
        </button>
      )}
    </div>
  );
}
