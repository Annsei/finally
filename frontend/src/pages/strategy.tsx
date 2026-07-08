/**
 * strategy.tsx — /strategy?id=X detail page (P2 §8).
 *
 * Static-export hydration (same pattern as /symbol?c=…): on first render
 * router.query is {} — the page shows the `strategy-empty` placeholder until
 * the query resolves, then mounts the detail view.
 *
 * Sections:
 *   header               name + SymbolLink + status chip + lifecycle controls:
 *                        strategy-deploy (soft gate: runs_count === 0 needs a
 *                        second confirming click — the backend never blocks),
 *                        strategy-pause (pause/resume), strategy-archive
 *                        (second-click confirm)
 *   strategy-config      human-readable entry/exits/sizing summary
 *                        (conditionText/exitsText/sizingText — i18n, money via
 *                        formatMoney, shares via formatShares)
 *   strategy-performance StatCard grid over GET …/performance + the 0-baseline
 *                        realized-P&L EquityChart (BaselineSeries base 0)
 *   backtests            strategy-run-backtest (days/runs → POST
 *                        /api/backtest/runs {strategy_id}), run-row-${id} list,
 *                        and the two-run side-by-side runs-compare table
 *
 * Pure helpers (exported for jest): conditionText, exitsText, sizingText,
 * compareRows.
 */
import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/compat/router';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import SymbolLink from '@/components/SymbolLink';
import EquityChart, { equityColors } from '@/components/backtest/EquityChart';
import StatCard, { signed, pnlClass } from '@/components/backtest/StatCard';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile, type MarketProfile } from '@/lib/marketProfile';
import { useT, type TFunction } from '@/lib/i18n';
import { formatMoney, formatShares } from '@/lib/format';
import type {
  BacktestRunListItem,
  BacktestRunsListResponse,
  StrategyCondition,
  StrategyConditionGroup,
  StrategyExits,
  StrategyPerformanceResponse,
  StrategyResponse,
  StrategySizing,
  StrategyStatus,
} from '@/types/market';

type MoneyOpts = { currency_symbol: string; locale: string };

// ---------------------------------------------------------------------------
// Human-readable config summaries (P2 §8 conditionText) — pure, i18n-driven.
// ---------------------------------------------------------------------------

/** One condition → a translated sentence. Money values go through formatMoney. */
function singleConditionText(cond: StrategyCondition, t: TFunction, money: MoneyOpts): string {
  const op = t(`strategy.cond.${cond.op}`);
  const params = cond.params ?? {};
  switch (cond.field) {
    case 'price':
      return t('strategy.cond.price', {
        op,
        sym: '',
        value: formatMoney(cond.value ?? 0, money),
      });
    case 'day_change_pct':
      return t('strategy.cond.day_change_pct', { op, value: cond.value ?? 0 });
    case 'ma':
      return t('strategy.cond.ma', {
        op,
        period: params.period ?? 20,
        value: cond.value ?? 0,
      });
    case 'ma_cross':
    case 'ema_cross':
      return t(`strategy.cond.${cond.field}.${cond.op}`, {
        fast: params.fast ?? 5,
        slow: params.slow ?? 20,
      });
    case 'rsi':
      return t('strategy.cond.rsi', {
        op,
        period: params.period ?? 14,
        value: cond.value ?? 0,
      });
    case 'window_high':
      return t('strategy.cond.window_high', { minutes: params.minutes ?? 60 });
    case 'window_low':
      return t('strategy.cond.window_low', { minutes: params.minutes ?? 60 });
    case 'pullback_from_high_pct':
      return t('strategy.cond.pullback_from_high_pct', {
        minutes: params.minutes ?? 60,
        value: cond.value ?? 0,
      });
    default:
      // Unknown backend field — degrade to a raw but readable form.
      return `${cond.field} ${op} ${cond.value ?? ''}`.trim();
  }
}

/** Condition group → "all of: A · B" (i18n joiner + per-field sentences). */
export function conditionText(
  group: StrategyConditionGroup | undefined | null,
  t: TFunction,
  money: MoneyOpts
): string {
  if (!group || typeof group !== 'object') return '—';
  const mode: 'all' | 'any' = 'all' in group ? 'all' : 'any';
  const conditions = ('all' in group ? group.all : group.any) ?? [];
  if (conditions.length === 0) return '—';
  const sentences = conditions.map((cond) => singleConditionText(cond, t, money));
  return `${t(`strategy.cond.${mode}`)}: ${sentences.join(' · ')}`;
}

/** Exits → "TP 4% · SL 3%" (or the i18n "No exits" placeholder). */
export function exitsText(exits: StrategyExits | undefined | null, t: TFunction): string {
  const parts: string[] = [];
  if (exits?.take_profit_pct != null)
    parts.push(t('strategy.exit.take_profit_pct', { value: exits.take_profit_pct }));
  if (exits?.stop_loss_pct != null)
    parts.push(t('strategy.exit.stop_loss_pct', { value: exits.stop_loss_pct }));
  if (exits?.trailing_stop_pct != null)
    parts.push(t('strategy.exit.trailing_stop_pct', { value: exits.trailing_stop_pct }));
  if (exits?.max_holding_days != null)
    parts.push(t('strategy.exit.max_holding_days', { value: exits.max_holding_days }));
  return parts.length > 0 ? parts.join(' · ') : t('strategy.exit.none');
}

/** Sizing → "Fixed qty 5" / "20% of cash" (shares via formatShares). */
export function sizingText(
  sizing: StrategySizing | undefined | null,
  t: TFunction,
  profile: Pick<MarketProfile, 'lot_size'>
): string {
  if (!sizing) return '—';
  if (sizing.mode === 'cash_pct') return t('strategy.sizing.cash_pct', { pct: sizing.pct });
  return t('strategy.sizing.fixed_qty', { qty: formatShares(sizing.qty, profile) });
}

// ---------------------------------------------------------------------------
// runs-compare shaping (P2 §8) — two persisted runs → side-by-side stat rows.
// ---------------------------------------------------------------------------
export interface CompareRow {
  label: string;
  a: string;
  b: string;
  aClass?: string;
  bClass?: string;
}

export function compareRows(
  a: BacktestRunListItem,
  b: BacktestRunListItem,
  t: TFunction
): CompareRow[] {
  const pct = (v: number) => `${signed(v)}%`;
  const winRate = (v: number | null) => (v != null ? `${Math.round(v * 100)}%` : '—');
  const factor = (v: number | null) => (v != null ? v.toFixed(2) : '—');
  return [
    {
      label: t('backtest.statReturn'),
      a: pct(a.stats.total_return_pct),
      b: pct(b.stats.total_return_pct),
      aClass: pnlClass(a.stats.total_return_pct),
      bClass: pnlClass(b.stats.total_return_pct),
    },
    {
      label: t('backtest.statBuyHold'),
      a: pct(a.stats.buy_hold_return_pct),
      b: pct(b.stats.buy_hold_return_pct),
      aClass: pnlClass(a.stats.buy_hold_return_pct),
      bClass: pnlClass(b.stats.buy_hold_return_pct),
    },
    {
      label: t('backtest.statMaxDd'),
      a: `−${a.stats.max_drawdown_pct.toFixed(2)}%`,
      b: `−${b.stats.max_drawdown_pct.toFixed(2)}%`,
    },
    {
      label: t('backtest.statWinRate'),
      a: winRate(a.stats.win_rate),
      b: winRate(b.stats.win_rate),
    },
    {
      label: t('backtest.statRoundTrips'),
      a: String(a.stats.round_trips),
      b: String(b.stats.round_trips),
    },
    {
      label: t('backtest.statProfitFactor'),
      a: factor(a.stats.profit_factor),
      b: factor(b.stats.profit_factor),
    },
    {
      label: t('backtest.days'),
      a: String(a.days),
      b: String(b.days),
    },
  ];
}

// ---------------------------------------------------------------------------
// Detail view
// ---------------------------------------------------------------------------
const sectionClass =
  'border border-terminal-border rounded bg-terminal-surface/30 flex flex-col min-h-0 shrink-0';
const sectionTitleClass =
  'px-2 py-1.5 text-xs font-semibold text-terminal-muted uppercase tracking-wider border-b border-terminal-border shrink-0';
const inputClass =
  'px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50';
const labelClass = 'text-xs font-semibold text-terminal-muted uppercase tracking-wider';

const STATUS_CHIP: Record<StrategyStatus, string> = {
  draft: 'text-terminal-muted border-terminal-border',
  live: 'text-terminal-up border-terminal-up/60',
  paused: 'text-terminal-amber border-terminal-amber/60',
  archived: 'text-terminal-muted border-terminal-border',
};

function StrategyDetail({ id }: { id: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const chartColors = equityColors(profile.up_is_red);

  const strategyKey = `/api/strategies/${id}`;
  const { data, error, mutate: mutateStrategy } = useSWR<StrategyResponse>(strategyKey, fetcher, {
    refreshInterval: 5000,
  });
  const strategy = data?.strategy;

  const { data: perfData } = useSWR<StrategyPerformanceResponse>(
    `/api/strategies/${id}/performance`,
    fetcher,
    { refreshInterval: 10_000 }
  );

  const runsKey = `/api/backtest/runs?strategy_id=${encodeURIComponent(id)}`;
  const { data: runsData, mutate: mutateRuns } = useSWR<BacktestRunsListResponse>(
    runsKey,
    fetcher
  );
  const runs = runsData?.runs ?? [];

  // Lifecycle controls -------------------------------------------------------
  const [deployArmed, setDeployArmed] = useState(false);
  const [archiveArmed, setArchiveArmed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [patching, setPatching] = useState(false);

  const patchStatus = async (status: StrategyStatus) => {
    setActionError(null);
    setPatching(true);
    try {
      const res = await fetch(strategyKey, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${res.status}`);
      }
      await mutateStrategy();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setPatching(false);
    }
  };

  const deploy = () => {
    if (!strategy) return;
    // Soft gate (P2 §8): deploying a never-backtested draft needs a second,
    // confirming click. The backend never blocks — this is UI-side friction.
    // Resuming a paused strategy is not gated.
    if (strategy.status === 'draft' && strategy.runs_count === 0 && !deployArmed) {
      setDeployArmed(true);
      return;
    }
    setDeployArmed(false);
    void patchStatus('live');
  };

  const archive = () => {
    if (!archiveArmed) {
      setArchiveArmed(true);
      return;
    }
    setArchiveArmed(false);
    void patchStatus('archived');
  };

  // Backtest launcher --------------------------------------------------------
  const [days, setDays] = useState('30');
  const [runsInput, setRunsInput] = useState('1');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const runBacktest = async () => {
    setRunError(null);
    setRunning(true);
    try {
      const res = await fetch('/api/backtest/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_id: id,
          days: Number(days),
          runs: Number(runsInput),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `${t('backtest.errFailed')} (${res.status})`);
      }
      await mutateRuns();
      await mutateStrategy(); // runs_count moved — the soft deploy gate reads it
    } catch (e) {
      setRunError(e instanceof Error ? e.message : t('backtest.errFailed'));
    } finally {
      setRunning(false);
    }
  };

  // Compare selection (max two) ----------------------------------------------
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const toggleCompare = (runId: string) => {
    setCompareIds((prev) => {
      if (prev.includes(runId)) return prev.filter((x) => x !== runId);
      if (prev.length >= 2) return prev;
      return [...prev, runId];
    });
  };
  const compared = compareIds
    .map((runId) => runs.find((r) => r.id === runId))
    .filter((r): r is BacktestRunListItem => r != null);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-terminal-muted text-xs">
        {t('strategy.notFound')}
      </div>
    );
  }
  if (!strategy) {
    return (
      <div className="flex items-center justify-center h-full text-terminal-muted text-xs">
        {t('runs.loading')}
      </div>
    );
  }

  const perfStats = perfData?.stats;
  const perfPnlColor =
    perfStats == null || perfStats.realized_pnl === 0
      ? 'text-terminal-text'
      : perfStats.realized_pnl > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 overflow-auto">
      {/* Header: name / symbol / status + lifecycle controls */}
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <h1
          data-testid="strategy-title"
          className="text-xl font-semibold text-terminal-text tracking-wide"
        >
          {strategy.name}
        </h1>
        <SymbolLink code={strategy.ticker} className="text-sm text-terminal-muted" />
        <span
          data-testid="strategy-status"
          className={`text-[9px] font-semibold px-1 py-0.5 rounded border uppercase tracking-wider ${
            STATUS_CHIP[strategy.status] ?? STATUS_CHIP.draft
          }`}
        >
          {t(`strategy.status.${strategy.status}`)}
        </span>

        <span className="ml-auto flex items-center gap-2">
          {(strategy.status === 'draft' || strategy.status === 'paused') && (
            <button
              type="button"
              data-testid="strategy-deploy"
              onClick={deploy}
              disabled={patching}
              className={`px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider text-white disabled:opacity-50 ${
                deployArmed ? 'ring-1 ring-terminal-amber' : ''
              }`}
              style={{ backgroundColor: '#753991' }}
            >
              {strategy.status === 'paused'
                ? t('strategy.resume')
                : deployArmed
                  ? t('strategy.deployConfirm')
                  : t('strategy.deploy')}
            </button>
          )}
          {strategy.status === 'live' && (
            <button
              type="button"
              data-testid="strategy-pause"
              onClick={() => void patchStatus('paused')}
              disabled={patching}
              className="px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider border border-terminal-amber/60 text-terminal-amber disabled:opacity-50"
            >
              {t('strategy.pause')}
            </button>
          )}
          {strategy.status !== 'archived' && (
            <button
              type="button"
              data-testid="strategy-archive"
              onClick={archive}
              disabled={patching}
              className="px-2 py-1 rounded text-[10px] font-semibold uppercase tracking-wider border border-terminal-border text-terminal-muted hover:text-terminal-down disabled:opacity-50"
            >
              {archiveArmed ? t('strategy.confirmArchive') : t('strategy.archive')}
            </button>
          )}
        </span>
      </div>

      {deployArmed && (
        <p data-testid="strategy-deploy-warning" className="text-xs text-terminal-amber shrink-0">
          {t('strategy.deployNoRunsWarning')}
        </p>
      )}
      {archiveArmed && (
        <p className="text-xs text-terminal-muted shrink-0">{t('strategy.archiveHint')}</p>
      )}
      {strategy.status === 'live' && (
        <p className="text-xs text-terminal-muted shrink-0">{t('strategy.pauseHint')}</p>
      )}
      {actionError && (
        <p data-testid="strategy-action-error" className="text-xs text-terminal-down shrink-0">
          {actionError}
        </p>
      )}

      {/* Config summary */}
      <section data-testid="strategy-config" className={sectionClass}>
        <h2 className={sectionTitleClass}>{t('strategy.configTitle')}</h2>
        <div className="p-2 text-xs flex flex-col gap-1">
          <p>
            <span className="text-terminal-muted mr-2">{t('strategy.entryTitle')}</span>
            <span className="text-terminal-text">{conditionText(strategy.entry, t, money)}</span>
          </p>
          <p>
            <span className="text-terminal-muted mr-2">{t('strategy.exitsTitle')}</span>
            <span className="text-terminal-text">{exitsText(strategy.exits, t)}</span>
          </p>
          <p>
            <span className="text-terminal-muted mr-2">{t('strategy.sizingTitle')}</span>
            <span className="text-terminal-text">{sizingText(strategy.sizing, t, profile)}</span>
          </p>
          <p>
            <span className="text-terminal-muted mr-2">{t('strategy.openPosition')}</span>
            {strategy.open_qty > 0 ? (
              <span className="text-terminal-text tabular-nums">
                {formatShares(strategy.open_qty, profile)}
                {strategy.open_price != null && ` @ ${formatMoney(strategy.open_price, money)}`}
              </span>
            ) : (
              <span className="text-terminal-muted">{t('strategy.noOpenPosition')}</span>
            )}
          </p>
        </div>
      </section>

      {/* Performance: StatCard grid + 0-baseline realized-P&L curve */}
      <section data-testid="strategy-performance" className={sectionClass}>
        <h2 className={sectionTitleClass}>{t('strategy.performanceTitle')}</h2>
        <div className="p-2">
          {perfStats ? (
            <>
              <div className="grid grid-cols-3 lg:grid-cols-6 gap-1.5">
                <StatCard
                  label={t('analytics.realizedPnl')}
                  value={`${perfStats.realized_pnl >= 0 ? '+' : '-'}${formatMoney(
                    Math.abs(perfStats.realized_pnl),
                    money
                  )}`}
                  className={perfPnlColor}
                  testid="strategy-perf-pnl"
                />
                <StatCard
                  label={t('backtest.statRoundTrips')}
                  value={String(perfStats.round_trips)}
                />
                <StatCard
                  label={t('backtest.statWinRate')}
                  value={
                    perfStats.win_rate != null ? `${Math.round(perfStats.win_rate * 100)}%` : '—'
                  }
                />
                <StatCard
                  label={t('backtest.statProfitFactor')}
                  value={perfStats.profit_factor != null ? perfStats.profit_factor.toFixed(2) : '—'}
                />
                <StatCard
                  label={t('backtest.statMaxDd')}
                  value={`−${perfStats.max_drawdown_pct.toFixed(2)}%`}
                />
                <StatCard label={t('backtest.statEntries')} value={String(perfStats.fires)} />
              </div>
              {(perfData?.equity_curve.length ?? 0) > 0 && (
                <div className="mt-2">
                  <EquityChart
                    equity={perfData!.equity_curve}
                    baseline={[]}
                    colors={chartColors}
                    baseValue={0}
                  />
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-terminal-muted">{t('analytics.loading')}</p>
          )}
        </div>
      </section>

      {/* Backtests: launcher + per-strategy run library + compare */}
      <section className={sectionClass}>
        <h2 className={sectionTitleClass}>{t('strategy.backtestTitle')}</h2>
        <div className="p-2 flex flex-col gap-2">
          <div className="flex items-end gap-2 flex-wrap">
            <div className="flex flex-col gap-1">
              <label htmlFor="st-bt-days" className={labelClass}>
                {t('backtest.days')}
              </label>
              <input
                id="st-bt-days"
                type="number"
                min="5"
                max="120"
                step="1"
                value={days}
                onChange={(e) => setDays(e.target.value)}
                disabled={running}
                className={`w-16 ${inputClass}`}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="st-bt-runs" className={labelClass}>
                {t('backtest.runs')}
              </label>
              <input
                id="st-bt-runs"
                type="number"
                min="1"
                max="50"
                step="1"
                value={runsInput}
                onChange={(e) => setRunsInput(e.target.value)}
                disabled={running}
                className={`w-16 ${inputClass}`}
              />
            </div>
            <button
              type="button"
              data-testid="strategy-run-backtest"
              onClick={() => void runBacktest()}
              disabled={running}
              className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              style={{ backgroundColor: '#753991' }}
            >
              {running ? t('strategy.running') : t('strategy.runBacktest')}
            </button>
            <span className="text-[10px] text-terminal-muted pb-2">
              {t('strategy.compareHint')}
            </span>
          </div>
          {runError && (
            <p data-testid="strategy-run-error" className="text-xs text-terminal-down">
              {runError}
            </p>
          )}

          {runs.length === 0 ? (
            <p className="text-xs text-terminal-muted">{t('runs.empty')}</p>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-terminal-muted border-b border-terminal-border">
                  <th className="w-6 py-1" aria-label={t('strategy.compare')} />
                  <th className="text-left py-1 font-semibold">{t('runs.colTime')}</th>
                  <th className="text-left py-1 font-semibold">{t('runs.colLabel')}</th>
                  <th className="text-right py-1 font-semibold">{t('runs.colReturn')}</th>
                  <th className="text-right py-1 font-semibold">{t('runs.colWinRate')}</th>
                  <th className="text-right py-1 pr-1 font-semibold">{t('runs.colMaxDd')}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    data-testid={`run-row-${run.id}`}
                    className="border-b border-terminal-border/60"
                  >
                    <td className="py-1">
                      <input
                        type="checkbox"
                        data-testid={`run-compare-${run.id}`}
                        aria-label={t('strategy.compare')}
                        checked={compareIds.includes(run.id)}
                        onChange={() => toggleCompare(run.id)}
                      />
                    </td>
                    <td className="py-1 tabular-nums text-terminal-muted">
                      <Link
                        href={{ pathname: '/run', query: { id: run.id } }}
                        className="hover:underline"
                      >
                        {new Date(run.created_at).toLocaleString(profile.locale, {
                          hour12: false,
                        })}
                      </Link>
                    </td>
                    <td className="py-1 text-terminal-text">
                      {run.label ?? `${run.days}d × ${run.runs}`}
                    </td>
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
                    <td className="text-right py-1 pr-1 tabular-nums text-terminal-text">
                      −{run.stats.max_drawdown_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {compared.length === 2 && (
            <div data-testid="runs-compare" className="mt-1 border border-terminal-border rounded">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-terminal-muted border-b border-terminal-border">
                    <th className="text-left py-1 pl-1 font-semibold">{t('strategy.compare')}</th>
                    {compared.map((run) => (
                      <th key={run.id} className="text-right py-1 pr-1 font-semibold">
                        {run.label ?? new Date(run.created_at).toLocaleString(profile.locale, {
                          hour12: false,
                        })}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {compareRows(compared[0], compared[1], t).map((row) => (
                    <tr key={row.label} className="border-b border-terminal-border/60">
                      <td className="py-1 pl-1 text-terminal-muted">{row.label}</td>
                      <td
                        className={`text-right py-1 pr-1 tabular-nums ${
                          row.aClass ?? 'text-terminal-text'
                        }`}
                      >
                        {row.a}
                      </td>
                      <td
                        className={`text-right py-1 pr-1 tabular-nums ${
                          row.bClass ?? 'text-terminal-text'
                        }`}
                      >
                        {row.b}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <Link href="/runs" className="self-start text-[10px] text-terminal-muted hover:underline">
            {t('runs.backToRuns')} →
          </Link>
        </div>
      </section>
    </div>
  );
}

export default function StrategyPage() {
  const router = useRouter();
  const raw = router?.query?.id;
  const id = typeof raw === 'string' && raw.trim() !== '' ? raw.trim() : null;
  const t = useT();

  return (
    <AppShell>
      {id === null ? (
        <div
          data-testid="strategy-empty"
          className="flex items-center justify-center h-full text-terminal-muted text-xs"
        >
          {t('strategy.empty')}
        </div>
      ) : (
        <StrategyDetail id={id} />
      )}
    </AppShell>
  );
}
