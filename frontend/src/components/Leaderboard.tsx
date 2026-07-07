/**
 * Leaderboard.tsx — season standings (PLATFORM_ROADMAP.md M4.2/4.3)
 *
 * Ranks every trader by live portfolio value; return % is measured against the
 * $10,000 season seed. The current user's row is highlighted. "Reset season"
 * uses a two-click confirm, archives standings server-side and restarts
 * everyone at $10k.
 */
import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { hardReload } from '@/lib/reload';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import type { LeaderboardResponse, AuthMeResponse } from '@/types/market';

function formatDate(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function Leaderboard() {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const { data, mutate } = useSWR<LeaderboardResponse>('/api/leaderboard', fetcher, {
    refreshInterval: 10_000,
  });
  const { data: me } = useSWR<AuthMeResponse>('/api/auth/me', fetcher);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Two-click confirm decays back to idle after 3s
  useEffect(() => {
    if (!confirming) return;
    confirmTimerRef.current = setTimeout(() => setConfirming(false), 3000);
    return () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    };
  }, [confirming]);

  const resetSeason = async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    setError(null);
    try {
      const res = await fetch('/api/season/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? t('board.resetFailedStatus', { status: res.status }));
      }
      // Every panel's numbers change after a reset — full refresh is honest
      hardReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('board.resetFailed'));
      await mutate();
    }
  };

  if (!data) {
    return <div className="p-4 text-terminal-muted text-xs">{t('board.loading')}</div>;
  }

  return (
    <div className="p-3" data-testid="leaderboard">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted">
          {t('board.seasonSince', { id: data.season.id, date: formatDate(data.season.started_at) })}
        </span>
        <button
          type="button"
          data-testid="season-reset"
          onClick={() => void resetSeason()}
          className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
            confirming
              ? 'text-terminal-down border-terminal-down'
              : 'text-terminal-muted border-terminal-border hover:text-terminal-text'
          }`}
        >
          {confirming ? t('board.confirmReset') : t('board.resetSeason')}
        </button>
      </div>

      <table data-testid="leaderboard-table" className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-1 font-semibold w-10">#</th>
            <th className="text-left py-1 font-semibold">{t('board.colTrader')}</th>
            <th className="text-right py-1 font-semibold">{t('board.colValue')}</th>
            <th className="text-right py-1 pr-1 font-semibold">{t('board.colReturn')}</th>
          </tr>
        </thead>
        <tbody>
          {data.entries.map((e) => {
            const isMe = me?.user?.id === e.user_id;
            const retColor =
              e.return_pct > 0
                ? 'text-terminal-up'
                : e.return_pct < 0
                  ? 'text-terminal-down'
                  : 'text-terminal-muted';
            return (
              <tr
                key={e.user_id}
                data-testid={`leaderboard-row-${e.user_id}`}
                className={`border-b border-terminal-border ${
                  isMe ? 'bg-terminal-surface border-l-2 border-l-terminal-accent' : ''
                }`}
              >
                <td className="py-1 pl-1 tabular-nums text-terminal-muted">{e.rank}</td>
                <td className="py-1 font-semibold text-terminal-text">
                  {e.name}
                  {isMe && <span className="ml-1 text-terminal-accent text-[10px]">{t('board.you')}</span>}
                </td>
                <td className="text-right py-1 tabular-nums text-terminal-text">
                  {formatMoney(e.total_value, money)}
                </td>
                <td className={`text-right py-1 pr-1 tabular-nums ${retColor}`}>
                  {e.return_pct > 0 ? '+' : ''}
                  {e.return_pct.toFixed(2)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {error && (
        <p data-testid="leaderboard-error" className="mt-2 text-xs text-terminal-down">
          {error}
        </p>
      )}
    </div>
  );
}
