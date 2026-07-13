/**
 * StrategyResearchCard.tsx — chat comparison card for one AI research batch
 * (D4 §3.2). Rendered by ChatPanel's actions block per ResearchOutcome — a
 * block card under the badge pills, sized for the 320px chat dock (ranked
 * candidate mini-rows, not a wide table).
 *
 * Per completed candidate (rank order): rank, name, hypothesis (clamped),
 * compact stats line (return / max drawdown / win rate / round trips) via
 * StatCard's `signed`/`pnlClass`, robustness score, /run + /strategy links,
 * and a Deploy button (`PATCH /api/strategies/{id} {"status": "live"}`).
 * Failed candidates render name + error in the down colour.
 *
 * Current lifecycle state is derived from
 * `useSWR('/api/strategies?status=all')` — the DEFAULT list view hides
 * archived rows server-side (strategies contract §6, pinned), so only the
 * status=all view can resolve an archived research draft — letting a
 * re-opened chat history show deployed/archived instead of a stale Deploy
 * button; ids missing even from the all view (deleted strategies) disable
 * it. Paused strategies re-enable Deploy — the same PATCH is the resume
 * transition (strategies.tsx toggleStatus). A successful deploy mutates
 * both this key and the plain '/api/strategies' key (AppShell's
 * STRATEGIES_REVALIDATE_KEY) so the strategies page stays fresh.
 *
 * Pinned testids (E2E contract §4): research-card, research-candidate,
 * research-deploy, research-recommended, research-deployed.
 */
import { useState } from 'react';
import Link from 'next/link';
import useSWR, { mutate as swrGlobalMutate } from 'swr';
import { fetcher } from '@/lib/fetcher';
import { useT } from '@/lib/i18n';
import { signed, pnlClass } from '@/components/backtest/StatCard';
import type {
  ResearchCandidateOutcome,
  ResearchOutcome,
  StrategiesResponse,
} from '@/types/market';

interface Props {
  outcome: ResearchOutcome;
  // AppShell's onNewTrade-style revalidation hook — fired after a successful
  // deploy so the strategies list (and the rest of the desk) refreshes.
  onDeployed?: () => void;
}

// Two-line clamp for hypotheses — same inline -webkit-box recipe as
// ChatPanel's BriefContent (no line-clamp utility dependency).
const CLAMP_2: React.CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
};

export default function StrategyResearchCard({ outcome, onDeployed }: Props) {
  const t = useT();
  // Current status per strategy_id (D4 §3.2). status=all is the only list
  // view that returns archived rows (the default view hides them — pinned),
  // so archived research drafts resolve to the archived chip instead of the
  // deleted-id fallback (runs.tsx precedent). No refreshInterval: a freshly
  // mounted card revalidates a stale key, and deploys mutate it directly.
  const { data: listData, mutate: mutateStrategies } = useSWR<StrategiesResponse>(
    '/api/strategies?status=all',
    fetcher
  );
  // undefined until the list loads — candidates then fall back to their
  // fresh-draft default (Deploy enabled) instead of flashing "disabled".
  const listed = Array.isArray(listData?.strategies) ? listData.strategies : undefined;

  const [busyId, setBusyId] = useState<string | null>(null);
  const [deployedIds, setDeployedIds] = useState<Record<string, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const deploy = async (id: string) => {
    if (busyId) return;
    setBusyId(id);
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      const res = await fetch(`/api/strategies/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'live' }),
      });
      if (!res.ok) {
        // Standard inline-fetch error convention: surface body.error when the
        // backend provides one, else a generic status fallback.
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `Deploy failed (${res.status})`);
      }
      setDeployedIds((prev) => ({ ...prev, [id]: true }));
      void mutateStrategies();
      // Also revalidate the default-view key — AppShell's
      // STRATEGIES_REVALIDATE_KEY ('/api/strategies', literal to avoid an
      // AppShell → ChatPanel → card import cycle) — so the strategies page
      // reflects the new live row. Guarded like AppShell: jest suites mock
      // 'swr' without the named export.
      if (typeof swrGlobalMutate === 'function') void swrGlobalMutate('/api/strategies');
      onDeployed?.();
    } catch (e) {
      setRowErrors((prev) => ({
        ...prev,
        [id]: e instanceof Error && e.message ? e.message : String(e),
      }));
    } finally {
      setBusyId(null);
    }
  };

  const candidates = outcome.candidates ?? [];
  // Render completed candidates in rank order (rank 1 first) regardless of
  // payload ordering; failed candidates follow in their original order.
  const completed = candidates
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => c.status === 'completed')
    .sort(
      (a, b) =>
        (a.c.rank ?? Number.MAX_SAFE_INTEGER) - (b.c.rank ?? Number.MAX_SAFE_INTEGER) ||
        a.i - b.i
    );
  const failed = candidates.map((c, i) => ({ c, i })).filter(({ c }) => c.status !== 'completed');

  const renderDeploySlot = (c: ResearchCandidateOutcome) => {
    const id = c.strategy_id;
    const known = id !== undefined && listed ? listed.find((s) => s.id === id) : undefined;
    // Fresh server truth outranks the local just-deployed flag: a strategy
    // deployed from this card and later archived elsewhere (the dock stays
    // mounted across navigation) must render the archived chip once the
    // status=all list reports it.
    if (known?.status === 'archived') {
      return (
        <span
          data-testid="research-archived"
          className="ml-auto shrink-0 px-2 py-0.5 rounded text-[10px] text-terminal-muted border border-terminal-border"
        >
          {t('research.archived')}
        </span>
      );
    }
    const isDeployed = (id !== undefined && deployedIds[id]) || known?.status === 'live';
    if (isDeployed) {
      return (
        <span
          data-testid="research-deployed"
          className="ml-auto shrink-0 px-2 py-0.5 rounded text-[10px] font-semibold"
          style={{ border: '1px solid #753991', color: '#b07cc6' }}
        >
          {t('research.deployed')}
        </span>
      );
    }
    // No id (defensive) or id missing from the loaded list (deleted) → the
    // button stays visible but disabled instead of PATCHing a dead resource.
    const unknown = id === undefined || (listed !== undefined && !known);
    return (
      <button
        type="button"
        data-testid="research-deploy"
        disabled={unknown || busyId === id}
        onClick={() => id !== undefined && void deploy(id)}
        className="ml-auto shrink-0 px-2 py-0.5 rounded text-[10px] font-semibold text-white disabled:opacity-50"
        style={{ backgroundColor: '#753991' }}
      >
        {busyId === id ? t('research.deploying') : t('research.deploy')}
      </button>
    );
  };

  return (
    <div
      data-testid="research-card"
      className="w-full mt-1 rounded border border-terminal-border bg-terminal-surface px-2 py-2 text-xs"
    >
      {/* Header: ticker · days · candidate count (completed/total) */}
      <div className="flex items-baseline gap-1.5 flex-wrap">
        <span className="font-semibold text-terminal-text">
          {t('research.title', { ticker: outcome.ticker })}
        </span>
        <span className="text-[10px] text-terminal-muted tabular-nums">
          {outcome.days != null ? `${t('research.days', { days: outcome.days })} · ` : ''}
          {completed.length}/{candidates.length}
        </span>
      </div>

      {/* Batch-level failure (e.g. the 2..4 candidate-count guard) */}
      {outcome.error && (
        <p className="mt-1 text-[11px] text-terminal-down leading-snug">{outcome.error}</p>
      )}

      {completed.map(({ c, i }) => {
        const isRecommended =
          outcome.recommended_strategy_id != null &&
          c.strategy_id === outcome.recommended_strategy_id;
        const rowError = c.strategy_id !== undefined ? rowErrors[c.strategy_id] : undefined;
        return (
          <div
            key={`ok-${i}`}
            data-testid="research-candidate"
            className="mt-1.5 pt-1.5 border-t border-terminal-border"
          >
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-terminal-muted tabular-nums">#{c.rank ?? '—'}</span>
              <span className="font-semibold text-terminal-text">{c.name}</span>
              {isRecommended && (
                <span
                  data-testid="research-recommended"
                  className="px-1 py-px rounded text-[10px] font-semibold"
                  style={{ border: '1px solid #ecad0a', color: '#ecad0a' }}
                >
                  {t('research.recommended')}
                </span>
              )}
            </div>
            {c.hypothesis && (
              <p className="mt-0.5 text-[10px] text-terminal-muted leading-snug" style={CLAMP_2}>
                {c.hypothesis}
              </p>
            )}
            {c.stats && (
              <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] tabular-nums">
                <span>
                  <span className="text-terminal-muted">{t('research.return')} </span>
                  <span className={pnlClass(c.stats.total_return_pct)}>
                    {signed(c.stats.total_return_pct, 1)}%
                  </span>
                </span>
                <span>
                  <span className="text-terminal-muted">{t('research.drawdown')} </span>
                  <span className="text-terminal-text">
                    {c.stats.max_drawdown_pct.toFixed(1)}%
                  </span>
                </span>
                <span>
                  <span className="text-terminal-muted">{t('research.winRate')} </span>
                  <span className="text-terminal-text">
                    {c.stats.win_rate != null ? `${Math.round(c.stats.win_rate * 100)}%` : '—'}
                  </span>
                </span>
                <span>
                  <span className="text-terminal-muted">{t('research.trades')} </span>
                  <span className="text-terminal-text">{c.stats.round_trips}</span>
                </span>
                {c.score != null && (
                  <span>
                    <span className="text-terminal-muted">{t('research.score')} </span>
                    <span className="font-semibold text-terminal-text">{c.score.toFixed(2)}</span>
                  </span>
                )}
              </div>
            )}
            <div className="mt-1 flex items-center gap-2 flex-wrap">
              {c.run_id && (
                <Link
                  href={{ pathname: '/run', query: { id: c.run_id } }}
                  className="text-[10px] text-terminal-blue hover:underline"
                >
                  {t('research.viewRun')}
                </Link>
              )}
              {c.strategy_id && (
                <Link
                  href={{ pathname: '/strategy', query: { id: c.strategy_id } }}
                  className="text-[10px] text-terminal-blue hover:underline"
                >
                  {t('research.viewStrategy')}
                </Link>
              )}
              {renderDeploySlot(c)}
            </div>
            {rowError && (
              <p
                data-testid="research-deploy-error"
                className="mt-0.5 text-[10px] text-terminal-down leading-snug"
              >
                {rowError}
              </p>
            )}
          </div>
        );
      })}

      {failed.map(({ c, i }) => (
        <div
          key={`fail-${i}`}
          data-testid="research-candidate"
          className="mt-1.5 pt-1.5 border-t border-terminal-border"
        >
          <div className="flex items-baseline gap-1.5 flex-wrap">
            <span className="font-semibold text-terminal-down">{c.name}</span>
            <span className="text-[10px] text-terminal-down leading-snug">
              {t('research.failed')}
              {c.error ? ` — ${c.error}` : ''}
            </span>
          </div>
        </div>
      ))}

      {/* Untraded winner ⇒ recommended_strategy_id is null (D4 §2.2) */}
      {completed.length > 0 && outcome.recommended_strategy_id == null && (
        <p className="mt-1.5 text-[10px] text-terminal-muted">{t('research.noRecommendation')}</p>
      )}
    </div>
  );
}
