/**
 * player.tsx — /player?u=<id> public player profile (P4 §4). Exported
 * statically as player/index.html (trailingSlash: true).
 *
 * Static-export hydration (same pattern as /symbol): on first render
 * router.query is {} — `u` is undefined until the router resolves, so the
 * page shows the `player-empty` placeholder and only mounts the detail view
 * once the query is ready.
 *
 * SUMMARY ONLY by contract: equity curve + position weight % — never
 * quantities, costs, or cash. A private profile (viewed by someone else)
 * renders the `player-private` empty state. The owner visiting their own
 * page gets the `player-privacy-toggle` (PATCH /api/players/me, optimistic
 * update + revalidate).
 */
import { useState } from 'react';
import { useRouter } from 'next/compat/router';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import PlayerEquity from '@/components/PlayerEquity';
import SymbolLink from '@/components/SymbolLink';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import { formatMoney } from '@/lib/format';
import type {
  AuthMeResponse,
  PlayerPositionWeight,
  PlayerProfileResponse,
} from '@/types/market';

/** Clamp a weight into 0..100 for the bar width. */
export function weightWidth(pct: number | undefined | null): number {
  if (pct == null || !Number.isFinite(pct)) return 0;
  return Math.min(Math.max(pct, 0), 100);
}

function formatSinceDate(iso: string, locale: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}

function WeightBar({ position }: { position: PlayerPositionWeight }) {
  const width = weightWidth(position.weight_pct);
  return (
    <div
      data-testid={`player-weight-${position.ticker}`}
      className="flex items-center gap-2 py-0.5"
    >
      <span className="w-16 shrink-0 text-xs font-semibold text-terminal-text truncate">
        <SymbolLink code={position.ticker} />
      </span>
      <span className="flex-1 h-2 rounded bg-terminal-border/40 overflow-hidden">
        {/* Neutral blue — portfolio weight has no up/down semantics */}
        <span
          data-weight={position.weight_pct}
          className="block h-full rounded"
          style={{ width: `${width}%`, backgroundColor: '#209dd7' }}
        />
      </span>
      <span className="w-12 shrink-0 text-right text-xs tabular-nums text-terminal-text">
        {position.weight_pct.toFixed(1)}%
      </span>
    </div>
  );
}

function PlayerDetail({ userId }: { userId: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };

  const playerKey = `/api/players/${encodeURIComponent(userId)}`;
  const { data, error, mutate } = useSWR<PlayerProfileResponse>(playerKey, fetcher);
  const { data: me } = useSWR<AuthMeResponse>('/api/auth/me', fetcher);
  const isMe = me?.user?.id === userId;

  // Privacy toggle (own page only) — optimistic session override over the
  // fetched flag; PATCH response is authoritative, then SWR revalidates.
  const [pubOverride, setPubOverride] = useState<boolean | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [patching, setPatching] = useState(false);

  if (error) {
    return (
      <div
        data-testid="player-notfound"
        className="flex items-center justify-center h-full text-terminal-muted text-xs"
      >
        {t('player.notFound')}
      </div>
    );
  }
  if (!data) {
    return <div className="p-4 text-xs text-terminal-muted">{t('player.loading')}</div>;
  }

  // The toggle reads the ACTUAL stored flag. The backend reports it as-is
  // in BOTH `public` and `profile_public` (the owner of a private profile
  // still gets the full payload, just with public:false); prefer
  // `profile_public` and fall back to `public` for payloads without it.
  const displayedPublic = pubOverride ?? data.profile_public ?? data.public;
  const togglePrivacy = async () => {
    if (patching) return;
    const prev = pubOverride;
    const next = !displayedPublic;
    setPubOverride(next); // optimistic
    setPatchError(null);
    setPatching(true);
    try {
      const res = await fetch('/api/players/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ public: next }),
      });
      if (!res.ok) throw new Error(`${t('player.privacyFailed')} (${res.status})`);
      const body = await res.json().catch(() => null);
      if (body && typeof body.public === 'boolean') setPubOverride(body.public);
      await mutate();
    } catch (e) {
      setPubOverride(prev); // revert the optimistic flip
      setPatchError(e instanceof Error && e.message ? e.message : t('player.privacyFailed'));
    } finally {
      setPatching(false);
    }
  };

  // Detail is visible whenever the payload carries it — the owner always gets
  // the full shape from the backend regardless of the privacy flag.
  const hasDetail =
    data.total_value != null ||
    (data.equity_curve?.length ?? 0) > 0 ||
    (data.positions_summary?.length ?? 0) > 0;
  const showPrivate = data.public === false && !hasDetail;

  const returnPct = data.return_pct;
  const returnColor =
    returnPct == null || returnPct === 0
      ? 'text-terminal-muted'
      : returnPct > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';

  const sectionClass =
    'border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0';
  const sectionTitleClass =
    'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';

  const positions = data.positions_summary ?? [];

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      {/* Header: name + since + rank/value/return + own-page privacy toggle */}
      <div className="flex items-baseline gap-3 flex-wrap shrink-0">
        <h1
          data-testid="player-name"
          className="text-xl font-semibold text-terminal-text tracking-wide"
        >
          {data.user.name}
        </h1>
        {data.user.created_at && (
          <span className="text-xs text-terminal-muted">
            {t('player.since', { date: formatSinceDate(data.user.created_at, profile.locale) })}
          </span>
        )}
        {!showPrivate && (
          <>
            <span className="text-sm text-terminal-muted">
              {t('player.rank')}{' '}
              <span data-testid="player-rank" className="text-terminal-accent font-semibold tabular-nums">
                {data.rank != null ? `#${data.rank}` : '—'}
              </span>
            </span>
            <span className="text-sm text-terminal-muted">
              {t('player.totalValue')}{' '}
              <span data-testid="player-total" className="text-terminal-text font-semibold tabular-nums">
                {formatMoney(data.total_value, money)}
              </span>
            </span>
            <span className="text-sm text-terminal-muted">
              {t('player.return')}{' '}
              <span data-testid="player-return" className={`font-semibold tabular-nums ${returnColor}`}>
                {returnPct != null
                  ? `${returnPct > 0 ? '+' : ''}${returnPct.toFixed(2)}%`
                  : '—'}
              </span>
            </span>
          </>
        )}
        {isMe && (
          <button
            type="button"
            data-testid="player-privacy-toggle"
            aria-pressed={displayedPublic}
            disabled={patching}
            onClick={() => void togglePrivacy()}
            className={`ml-auto px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider border transition-colors disabled:opacity-50 ${
              displayedPublic
                ? 'text-terminal-blue border-terminal-blue'
                : 'text-terminal-muted border-terminal-border'
            }`}
          >
            {displayedPublic ? t('player.privacyPublic') : t('player.privacyPrivate')}
          </button>
        )}
      </div>
      {patchError && (
        <p data-testid="player-privacy-error" className="text-xs text-terminal-down shrink-0">
          {patchError}
        </p>
      )}

      {showPrivate ? (
        <div
          data-testid="player-private"
          className="flex items-center justify-center flex-1 text-terminal-muted text-xs"
        >
          {t('player.private')}
        </div>
      ) : (
        <div className="flex flex-col gap-3 flex-1 min-h-0 overflow-auto">
          <section className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('player.equityTitle')}</h2>
            <div className="p-2">
              <PlayerEquity curve={data.equity_curve ?? []} />
            </div>
          </section>

          <section data-testid="player-weights" className={sectionClass}>
            <h2 className={sectionTitleClass}>{t('player.weightsTitle')}</h2>
            <div className="p-2">
              {positions.length === 0 ? (
                <p className="text-xs text-terminal-muted">{t('player.weightsEmpty')}</p>
              ) : (
                positions.map((p) => <WeightBar key={p.ticker} position={p} />)
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default function PlayerPage() {
  const router = useRouter();
  const raw = router?.query?.u;
  const userId = typeof raw === 'string' && raw.trim() !== '' ? raw.trim() : null;
  const t = useT();

  return (
    <AppShell>
      {userId === null ? (
        <div
          data-testid="player-empty"
          className="flex items-center justify-center h-full text-terminal-muted text-xs"
        >
          {t('player.empty')}
        </div>
      ) : (
        <PlayerDetail userId={userId} />
      )}
    </AppShell>
  );
}
