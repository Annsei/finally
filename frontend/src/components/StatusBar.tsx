/**
 * StatusBar.tsx — bottom status strip (FRONTEND_REALISM.md §3.3, M3.1 sessions)
 *
 * Left: session badge + keyboard hints. The badge polls GET /api/market/session:
 * OPEN (green) with a countdown to the close, CLOSED (red) with a countdown to
 * the reopen, or the static SIM 24/7 label when the sim runs without sessions.
 * Right: feed latency (age of the most recent SSE tick) and a live clock.
 * Re-renders on a 1s interval.
 */
import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import { fetcher } from '@/lib/fetcher';
import type { MarketSessionResponse } from '@/types/market';

function latestTickTs(prices: Record<string, { timestamp: number }>): number | null {
  let max: number | null = null;
  for (const key of Object.keys(prices)) {
    const ts = prices[key].timestamp;
    if (max === null || ts > max) max = ts;
  }
  return max;
}

function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return `${m}:${String(rest).padStart(2, '0')}`;
}

function SessionBadge({ now }: { now: number }) {
  const { data } = useSWR<MarketSessionResponse>('/api/market/session', fetcher, {
    refreshInterval: 5000,
  });

  if (!data || data.next_transition_at == null) {
    return (
      <span data-testid="session-badge" data-state="always-open" className="font-semibold text-terminal-accent">
        SIM 24/7
      </span>
    );
  }

  const remaining = data.next_transition_at - now / 1000;
  const isOpen = data.state === 'open';
  return (
    <span
      data-testid="session-badge"
      data-state={data.state}
      className={`font-semibold tabular-nums ${isOpen ? 'text-terminal-up' : 'text-terminal-down'}`}
    >
      {isOpen ? 'OPEN' : 'CLOSED'}
      <span className="ml-1 text-terminal-muted font-normal">
        {isOpen ? 'closes' : 'opens'} in {formatCountdown(remaining)}
      </span>
    </span>
  );
}

export default function StatusBar() {
  const prices = usePriceStore((s) => s.prices);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const lastTs = latestTickTs(prices);
  const ageSec = lastTs != null ? Math.max(0, now / 1000 - lastTs) : null;
  const feedColor =
    ageSec == null
      ? 'text-terminal-muted'
      : ageSec < 3
        ? 'text-terminal-up'
        : ageSec < 10
          ? 'text-terminal-amber'
          : 'text-terminal-down';
  const feedLabel =
    ageSec == null ? 'Feed: —' : `Feed: ${ageSec < 1 ? '<1' : Math.round(ageSec)}s ago`;

  return (
    <footer className="h-6 shrink-0 flex items-center justify-between px-4 border-t border-terminal-border bg-terminal-surface text-xs">
      <span className="flex items-center gap-3 text-terminal-muted">
        <SessionBadge now={now} />
        <span className="hidden md:inline">
          Shortcuts: <kbd className="px-1">/</kbd> search · <kbd className="px-1">↑↓</kbd> select ·{' '}
          <kbd className="px-1">B</kbd>/<kbd className="px-1">S</kbd> trade
        </span>
      </span>
      <span className="flex items-center gap-4 tabular-nums">
        <span data-testid="status-feed-latency" className={feedColor}>
          {feedLabel}
        </span>
        <span data-testid="status-clock" className="text-terminal-muted">
          {new Date(now).toLocaleTimeString('en-US', { hour12: false })}
        </span>
      </span>
    </footer>
  );
}
