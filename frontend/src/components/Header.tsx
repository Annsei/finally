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
import { useState } from 'react';
import useSWR from 'swr';
import { usePriceStore } from '@/stores/priceStore';
import type { PortfolioResponse, AuthMeResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
import { hardReload } from '@/lib/reload';

// M4.1 — name-only identity chip. Anonymous acts as the Guest ('default')
// user; signing in/out reloads so every SWR key refetches under the new cookie.
function AuthChip() {
  const { data } = useSWR<AuthMeResponse>('/api/auth/me', fetcher);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const user = data?.user;
  const isGuest = !user || user.id === 'default';

  const login = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `Sign-in failed (${res.status})`);
      }
      hardReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in failed');
      setPending(false);
    }
  };

  const logout = async () => {
    setPending(true);
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } finally {
      hardReload();
    }
  };

  if (!isGuest) {
    return (
      <span className="flex items-center gap-2 text-xs">
        <span data-testid="auth-user" className="text-terminal-text font-semibold">
          {user!.name}
        </span>
        <button
          type="button"
          data-testid="auth-logout"
          onClick={() => void logout()}
          disabled={pending}
          className="text-terminal-muted hover:text-terminal-down text-[10px] font-semibold uppercase tracking-wider disabled:opacity-50"
        >
          Sign out
        </button>
      </span>
    );
  }

  if (!editing) {
    return (
      <button
        type="button"
        data-testid="auth-signin"
        onClick={() => setEditing(true)}
        className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted hover:text-terminal-accent transition-colors"
      >
        Guest · Sign in
      </button>
    );
  }

  return (
    <span className="flex items-center gap-1">
      <input
        type="text"
        data-testid="auth-name-input"
        aria-label="Trader name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void login();
          if (e.key === 'Escape') setEditing(false);
        }}
        placeholder="Trader name…"
        maxLength={24}
        disabled={pending}
        autoFocus
        className="w-28 px-2 py-0.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue disabled:opacity-50"
      />
      <button
        type="button"
        data-testid="auth-submit"
        onClick={() => void login()}
        disabled={pending || !name.trim()}
        className="px-2 py-0.5 rounded text-[10px] font-semibold text-white disabled:opacity-50"
        style={{ backgroundColor: '#753991' }}
      >
        Go
      </button>
      {error && (
        <span data-testid="auth-error" className="text-[10px] text-terminal-down">
          {error}
        </span>
      )}
    </span>
  );
}

// Dot color map — amber for reconnecting so accent yellow stays reserved for row selection (UI-SPEC)
const DOT_COLORS: Record<'connected' | 'reconnecting' | 'disconnected', string> = {
  connected: 'bg-terminal-up',
  reconnecting: 'bg-terminal-amber',
  disconnected: 'bg-terminal-down',
};

export default function Header() {
  // Single-atom selector — avoids Zustand v5 "Maximum update depth exceeded" (RESEARCH Pitfall 2)
  const connectionStatus = usePriceStore((s) => s.connectionStatus);

  // Live prices for the Day P&L computation (re-renders per tick — the header
  // is small and every watchlist row already updates at the same cadence)
  const prices = usePriceStore((s) => s.prices);

  // SWR polling every 5s — satisfies FE-02 "live updating" for portfolio numbers (D-04)
  const { data } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher, {
    refreshInterval: 5000,
  });

  // Format number with US locale and 2 decimal places; fall back to '—' when undefined
  const fmt = (n: number | undefined) =>
    n !== undefined
      ? n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '—';

  // Day P&L = Σ qty × (price − prev_close) over positions (FRONTEND_REALISM §2.4).
  // undefined until portfolio AND a prev_close-bearing price exist for ≥1 position.
  const dayPnl = data?.positions?.length
    ? data.positions.reduce<number | undefined>((sum, p) => {
        const u = prices[p.ticker];
        if (!u || u.prev_close == null) return sum;
        return (sum ?? 0) + p.quantity * (u.price - u.prev_close);
      }, undefined)
    : data
      ? 0
      : undefined;
  const dayPnlColor =
    dayPnl === undefined || dayPnl === 0
      ? 'text-terminal-muted'
      : dayPnl > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';

  return (
    <header className="flex items-center justify-between px-4 py-2 border-b border-terminal-border bg-terminal-surface">
      {/* Brand + identity */}
      <span className="flex items-center gap-4">
        <span className="text-terminal-accent font-semibold text-lg tracking-wide">
          FinAlly
        </span>
        <AuthChip />
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

        {/* Realized P&L — lifetime, from closed trades (M1.4) */}
        <div className="flex flex-col items-end">
          <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
            Realized
          </span>
          <span
            data-testid="realized-pnl"
            className={`text-sm font-normal tabular-nums ${
              data?.realized_pnl == null || data.realized_pnl === 0
                ? 'text-terminal-muted'
                : data.realized_pnl > 0
                  ? 'text-terminal-up'
                  : 'text-terminal-down'
            }`}
          >
            {data?.realized_pnl != null
              ? `${data.realized_pnl > 0 ? '+' : data.realized_pnl < 0 ? '-' : ''}$${fmt(Math.abs(data.realized_pnl))}`
              : '—'}
          </span>
        </div>

        {/* Day P&L — positions only, vs previous close */}
        <div className="flex flex-col items-end">
          <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
            Day P&L
          </span>
          <span
            data-testid="day-pnl"
            className={`text-sm font-normal tabular-nums ${dayPnlColor}`}
          >
            {dayPnl !== undefined
              ? `${dayPnl > 0 ? '+' : dayPnl < 0 ? '-' : ''}$${fmt(Math.abs(dayPnl))}`
              : '—'}
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

        {/* Connection status dot — 8px circle, color driven by Zustand SSE state.
            data-testid/data-state form the E2E contract: state ∈ connected | reconnecting | disconnected */}
        <div
          data-testid="connection-status"
          data-state={connectionStatus}
          className={`w-2 h-2 rounded-full ${DOT_COLORS[connectionStatus]}`}
          title={connectionStatus}
        />
      </div>
    </header>
  );
}
