/**
 * Header.tsx — App header bar (FE-02)
 *
 * Data sources (per locked decision D-04):
 *   - Portfolio total value + cash: SWR polling GET /api/portfolio every 5s (REST, NOT Zustand)
 *   - Connection status dot: Zustand connectionStatus atom (SSE-driven via usePriceStream)
 *
 * DOT_COLORS uses Tailwind terminal color tokens:
 *   connected    → bg-terminal-up    (#22c55e green)
 *   reconnecting → bg-terminal-amber (#f59e0b amber, NOT accent yellow — keeps #ecad0a for row selection)
 *   disconnected → bg-terminal-down  (#ef4444 red)
 *
 * Typography (UI-SPEC):
 *   Portfolio total: text-xl / font-semibold (display size, 20px / weight 600)
 *   Cash balance:    text-sm / font-normal   (data size, 14px / weight 400)
 *   Labels:          text-xs / font-semibold (label size, 12px / weight 600)
 * Both numeric spans use tabular-nums to prevent layout shift on digit changes.
 */
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';

// Dot color map — amber for reconnecting so accent yellow stays reserved for row selection (UI-SPEC)
const DOT_COLORS: Record<'connected' | 'reconnecting' | 'disconnected', string> = {
  connected: 'bg-terminal-up',
  reconnecting: 'bg-terminal-amber',
  disconnected: 'bg-terminal-down',
};

export default function Header() {
  // Single-atom selector — avoids Zustand v5 "Maximum update depth exceeded" (RESEARCH Pitfall 2)
  const connectionStatus = usePriceStore((s) => s.connectionStatus);

  // SWR polling every 5s — satisfies FE-02 "live updating" for portfolio numbers (D-04)
  const { data } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher, {
    refreshInterval: 5000,
  });

  // Format number with US locale and 2 decimal places; fall back to '—' when undefined
  const fmt = (n: number | undefined) =>
    n !== undefined ? n.toLocaleString('en-US', { minimumFractionDigits: 2 }) : '—';

  return (
    <header className="flex items-center justify-between px-4 py-2 border-b border-terminal-border bg-terminal-surface">
      {/* Brand */}
      <span className="text-terminal-accent font-semibold text-lg tracking-wide">
        FinAlly
      </span>

      {/* Right cluster: Cash · Portfolio · Connection dot */}
      <div className="flex items-center gap-6">
        {/* Cash balance */}
        <div className="flex flex-col items-end">
          <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
            Cash
          </span>
          <span className="text-sm font-normal text-terminal-text tabular-nums">
            ${fmt(data?.cash)}
          </span>
        </div>

        {/* Portfolio total value — display size (largest live number) */}
        <div className="flex flex-col items-end">
          <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
            Portfolio
          </span>
          <span className="text-xl font-semibold text-terminal-text tabular-nums">
            ${fmt(data?.total_value)}
          </span>
        </div>

        {/* Connection status dot — 8px circle, color driven by Zustand SSE state */}
        <div
          className={`w-2 h-2 rounded-full ${DOT_COLORS[connectionStatus]}`}
          title={connectionStatus}
        />
      </div>
    </header>
  );
}
