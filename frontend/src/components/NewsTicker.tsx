/**
 * NewsTicker.tsx — scrolling market-event feed (FRONTEND_REALISM.md §3.1)
 *
 * The simulator's sudden 2-5% moves surface as events via
 * GET /api/market/events (polled — news cadence doesn't need SSE). Content is
 * rendered twice inside .news-ticker-track for a seamless CSS marquee loop;
 * hover pauses the animation (see globals.css).
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import type { MarketEvent, MarketEventsResponse } from '@/types/market';
import { useT } from '@/lib/i18n';

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-US', { hour12: false });
}

function EventItem({ ev }: { ev: MarketEvent }) {
  // Direction colour flips with the market (CSS var); the news feed reads as
  // green-up / red-down on US, red-up / green-down on the A-share market.
  const color = ev.direction === 'up' ? 'var(--color-up)' : 'var(--color-down)';
  return (
    <span className="inline-flex items-baseline gap-1 px-4 text-xs" data-testid={`news-item-${ev.id}`}>
      <span className="text-terminal-muted tabular-nums">{formatTime(ev.timestamp)}</span>
      <span style={{ color }}>{ev.direction === 'up' ? '▲' : '▼'}</span>
      {/* LLM narrative when enriched (M3.2), template headline otherwise */}
      <span className="text-terminal-text">{ev.narrative ?? ev.headline}</span>
    </span>
  );
}

export default function NewsTicker() {
  const t = useT();
  const { data } = useSWR<MarketEventsResponse>('/api/market/events', fetcher, {
    refreshInterval: 5000,
  });
  const events = data?.events ?? [];

  return (
    <div
      data-testid="news-ticker"
      className="h-6 flex items-center overflow-hidden border-b border-terminal-border bg-terminal-surface/60"
    >
      {events.length === 0 ? (
        <span className="px-4 text-xs text-terminal-muted">
          {t('news.empty')}
        </span>
      ) : (
        <div className="news-ticker-track">
          {/* duplicated content = seamless loop */}
          {[0, 1].map((copy) => (
            <span key={copy} aria-hidden={copy === 1}>
              {events.map((ev) => (
                <EventItem key={`${copy}-${ev.id}`} ev={ev} />
              ))}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
