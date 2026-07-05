/**
 * StatusBar.tsx — bottom status strip (FRONTEND_REALISM.md §3.3)
 *
 * Left: session label + keyboard hints. Right: feed latency (age of the most
 * recent SSE tick — the data-source health readout every terminal has) and a
 * live local clock. Re-renders on a 1s interval.
 */
import { useEffect, useState } from 'react';
import { usePriceStore } from '@/stores/priceStore';

function latestTickTs(prices: Record<string, { timestamp: number }>): number | null {
  let max: number | null = null;
  for (const key of Object.keys(prices)) {
    const ts = prices[key].timestamp;
    if (max === null || ts > max) max = ts;
  }
  return max;
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
        <span className="font-semibold text-terminal-accent">SIM 24/7</span>
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
