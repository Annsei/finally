/**
 * runs.tsx — /runs run library page (P2 §8). Exported statically as
 * runs/index.html (trailingSlash: true).
 *
 * Full-library table over SWR GET /api/backtest/runs (list shape: stats only,
 * no curves) with client-side filters: a ticker text input plus a strategy
 * dropdown (names resolved via GET /api/strategies?status=all so archived
 * strategies still label their historical runs).
 *
 * Rows (`run-row-${id}`): time / SymbolLink + data-source badge
 * (`run-source-${id}`, D1 §5 — pre-D1 rows render as synthetic) /
 * strategy-name link / label / return % / win rate / max DD (direction
 * colours via terminal-up/down) — row click → /run?id=X. Delete
 * (`run-delete-${id}`) is a two-click confirm.
 *
 * Pure helper (exported for jest): filterRuns.
 */
import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/compat/router';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import SymbolLink from '@/components/SymbolLink';
import { signed, pnlClass } from '@/components/backtest/StatCard';
import SourceBadge, { runSourceKind } from '@/components/backtest/SourceBadge';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import type {
  BacktestRunListItem,
  BacktestRunsListResponse,
  StrategiesResponse,
} from '@/types/market';

export const RUNS_KEY = '/api/backtest/runs?limit=200';

/**
 * Client-side filter: case-insensitive ticker substring + exact strategy id
 * ('' = no filter on that axis).
 */
export function filterRuns(
  runs: BacktestRunListItem[],
  ticker: string,
  strategyId: string
): BacktestRunListItem[] {
  const needle = ticker.trim().toUpperCase();
  return runs.filter(
    (run) =>
      (needle === '' || run.ticker.toUpperCase().includes(needle)) &&
      (strategyId === '' || run.strategy_id === strategyId)
  );
}

export default function RunsPage() {
  const t = useT();
  const router = useRouter();
  const profile = useMarketProfile();

  const { data, mutate } = useSWR<BacktestRunsListResponse>(RUNS_KEY, fetcher);
  const { data: strategiesData } = useSWR<StrategiesResponse>(
    '/api/strategies?status=all',
    fetcher
  );
  const strategies = strategiesData?.strategies ?? [];
  const nameById = new Map(strategies.map((s) => [s.id, s.name]));

  const [tickerFilter, setTickerFilter] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('');
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const openRun = (id: string) => {
    void router?.push({ pathname: '/run', query: { id } });
  };

  const remove = async (id: string) => {
    if (armedDelete !== id) {
      setArmedDelete(id);
      return;
    }
    setArmedDelete(null);
    setDeleteError(null);
    try {
      const res = await fetch(`/api/backtest/runs/${id}`, { method: 'DELETE' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${t('runs.deleteFailed')} (${res.status})`);
      }
      await mutate();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : t('runs.deleteFailed'));
    }
  };

  const runs = data?.runs;
  const visible = filterRuns(runs ?? [], tickerFilter, strategyFilter);

  const inputClass =
    'px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted';

  return (
    <AppShell>
      <div className="flex flex-col gap-3 h-full min-h-0">
        <div className="flex items-center gap-3 flex-wrap shrink-0">
          <h1 className="text-xl font-semibold text-terminal-text tracking-wide">
            {t('runs.title')}
          </h1>
          <input
            type="text"
            data-testid="runs-filter-ticker"
            aria-label={t('runs.filterTickerAria')}
            placeholder={t('runs.filterTicker')}
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
            className={`w-36 ${inputClass}`}
          />
          <select
            data-testid="runs-filter-strategy"
            aria-label={t('runs.filterStrategy')}
            value={strategyFilter}
            onChange={(e) => setStrategyFilter(e.target.value)}
            className={inputClass}
          >
            <option value="">{t('runs.allStrategies')}</option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        {deleteError && (
          <p data-testid="runs-delete-error" className="text-xs text-terminal-down shrink-0">
            {deleteError}
          </p>
        )}

        <section className="flex-1 min-h-0 border border-terminal-border rounded bg-terminal-surface/30 overflow-auto">
          {runs == null ? (
            <p className="p-3 text-xs text-terminal-muted">{t('runs.loading')}</p>
          ) : visible.length === 0 ? (
            <p className="p-3 text-xs text-terminal-muted">{t('runs.empty')}</p>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-terminal-muted border-b border-terminal-border sticky top-0 bg-terminal-bg">
                  <th className="text-left py-1 pl-1 font-semibold">{t('runs.colTime')}</th>
                  <th className="text-left py-1 font-semibold">{t('runs.colTicker')}</th>
                  <th className="text-left py-1 font-semibold">{t('runs.colStrategy')}</th>
                  <th className="text-left py-1 font-semibold">{t('runs.colLabel')}</th>
                  <th className="text-right py-1 font-semibold">{t('runs.colReturn')}</th>
                  <th className="text-right py-1 font-semibold">{t('runs.colWinRate')}</th>
                  <th className="text-right py-1 font-semibold">{t('runs.colMaxDd')}</th>
                  <th className="text-right py-1 pr-1" aria-label={t('runs.delete')} />
                </tr>
              </thead>
              <tbody>
                {visible.map((run) => (
                  <tr
                    key={run.id}
                    data-testid={`run-row-${run.id}`}
                    onClick={() => openRun(run.id)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return;
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openRun(run.id);
                      }
                    }}
                    tabIndex={0}
                    aria-label={`${run.ticker} ${run.label ?? ''}`.trim()}
                    className="cursor-pointer border-b border-terminal-border/60 hover:bg-terminal-surface/50 focus:outline focus:outline-1 focus:outline-terminal-accent"
                  >
                    <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                      {new Date(run.created_at).toLocaleString(profile.locale, { hour12: false })}
                    </td>
                    <td className="py-1 font-semibold" onClick={(e) => e.stopPropagation()}>
                      <span className="flex items-center gap-1">
                        <SymbolLink code={run.ticker} />
                        {/* D1 §5 — data-source badge (pre-D1 rows → synthetic) */}
                        <SourceBadge
                          testid={`run-source-${run.id}`}
                          source={runSourceKind(run)}
                          t={t}
                        />
                      </span>
                    </td>
                    <td className="py-1" onClick={(e) => e.stopPropagation()}>
                      {run.strategy_id != null && nameById.has(run.strategy_id) ? (
                        <Link
                          href={{ pathname: '/strategy', query: { id: run.strategy_id } }}
                          data-testid={`run-strategy-link-${run.id}`}
                          className="hover:underline text-terminal-text"
                        >
                          {nameById.get(run.strategy_id)}
                        </Link>
                      ) : (
                        <span className="text-terminal-muted">—</span>
                      )}
                    </td>
                    <td className="py-1 text-terminal-muted">{run.label ?? '—'}</td>
                    <td
                      className={`text-right py-1 tabular-nums ${pnlClass(
                        run.stats.total_return_pct
                      )}`}
                    >
                      {signed(run.stats.total_return_pct)}%
                    </td>
                    <td className="text-right py-1 tabular-nums text-terminal-text">
                      {run.stats.win_rate != null
                        ? `${Math.round(run.stats.win_rate * 100)}%`
                        : '—'}
                    </td>
                    <td className="text-right py-1 tabular-nums text-terminal-text">
                      −{run.stats.max_drawdown_pct.toFixed(2)}%
                    </td>
                    <td className="text-right py-1 pr-1">
                      <button
                        type="button"
                        data-testid={`run-delete-${run.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(run.id);
                        }}
                        className={`text-[10px] font-semibold uppercase tracking-wider ${
                          armedDelete === run.id
                            ? 'text-terminal-down'
                            : 'text-terminal-muted hover:text-terminal-down'
                        }`}
                      >
                        {armedDelete === run.id ? t('runs.confirmDelete') : t('runs.delete')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </AppShell>
  );
}
