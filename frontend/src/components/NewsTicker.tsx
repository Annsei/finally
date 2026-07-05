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

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-US', { hour12: false });
}

function EventItem({ ev }: { ev: MarketEvent }) {
  const color = ev.direction === 'up' ? '#22c55e' : '#ef4444';
  return (
    <span className="inline-flex items-baseline gap-1 px-4 text-xs" data-testid={`news-item-${ev.id}`}>
      <span className="text-terminal-muted tabular-nums">{formatTime(ev.timestamp)}</span>
      <span style={{ color }}>{ev.direction === 'up' ? '▲' : '▼'}</span>
      <span className="text-terminal-text">{ev.headline}</span>
    </span>
  );
}

export default function NewsTicker() {
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
          Market events appear here — watching for unusual moves…
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
