/**
 * run.tsx — /run?id=X persisted-backtest detail page (P2 §8).
 *
 * Static-export hydration (same pattern as /symbol and /strategy): while
 * router.query is unresolved the page shows the `run-empty` placeholder;
 * once the id resolves it fetches GET /api/backtest/runs/{id} and renders
 * `run-detail` — the full composition of the extracted backtest components:
 * StatsGrid + EquityChart + RunsSummaryStrip (when runs > 1) + TradesBlotter,
 * plus back-links to the run library and, when attributed, the strategy.
 */
import Link from 'next/link';
import { useRouter } from 'next/compat/router';
import useSWR from 'swr';
import AppShell from '@/components/AppShell';
import SymbolLink from '@/components/SymbolLink';
import EquityChart, { equityColors } from '@/components/backtest/EquityChart';
import StatsGrid from '@/components/backtest/StatsGrid';
import RunsSummaryStrip from '@/components/backtest/RunsSummaryStrip';
import TradesBlotter from '@/components/backtest/TradesBlotter';
import SourceBadge, { runSourceKind, runDateRange } from '@/components/backtest/SourceBadge';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';
import type { BacktestRunResponse } from '@/types/market';

function RunDetail({ id }: { id: string }) {
  const t = useT();
  const profile = useMarketProfile();
  const chartColors = equityColors(profile.up_is_red);

  const { data, error } = useSWR<BacktestRunResponse>(`/api/backtest/runs/${id}`, fetcher);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-terminal-muted text-xs">
        {t('runs.notFound')}
      </div>
    );
  }
  const run = data?.run;
  if (!run) {
    return (
      <div className="flex items-center justify-center h-full text-terminal-muted text-xs">
        {t('runs.loading')}
      </div>
    );
  }

  const days = typeof run.config.days === 'number' ? run.config.days : null;
  const runsCount = typeof run.config.runs === 'number' ? run.config.runs : null;
  const seed = typeof run.config.seed === 'number' ? run.config.seed : null;

  return (
    <div data-testid="run-detail" className="flex flex-col gap-3 h-full min-h-0 overflow-auto">
      {/* Header: title / symbol / label / created / config line + back links */}
      <div className="flex items-baseline gap-3 flex-wrap shrink-0">
        <h1 className="text-xl font-semibold text-terminal-text tracking-wide">
          {t('runs.detailTitle')}
        </h1>
        <SymbolLink code={run.config.ticker} className="text-sm font-semibold" />
        {run.label && (
          <span
            data-testid="run-label"
            className="text-[10px] font-semibold px-1 py-0.5 rounded border border-terminal-border text-terminal-muted"
          >
            {run.label}
          </span>
        )}
        {/* Data-source badge + evaluated date range (D1 §5, additive) */}
        <SourceBadge
          testid="run-source-badge"
          source={runSourceKind(run.config)}
          dateRange={runDateRange(run.config)}
          t={t}
        />
        <span className="text-xs text-terminal-muted tabular-nums">
          {new Date(run.created_at).toLocaleString(profile.locale, { hour12: false })}
        </span>
        <span className="text-xs text-terminal-muted tabular-nums">
          {days != null && `${t('backtest.days')} ${days}`}
          {runsCount != null && ` · ${t('backtest.runs')} ${runsCount}`}
          {seed != null && ` · seed ${seed}`}
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px]">
          <Link
            href="/runs"
            data-testid="run-back-to-runs"
            className="text-terminal-muted hover:text-terminal-text hover:underline uppercase tracking-wider font-semibold"
          >
            ← {t('runs.backToRuns')}
          </Link>
          {run.strategy_id != null && (
            <Link
              href={{ pathname: '/strategy', query: { id: run.strategy_id } }}
              data-testid="run-back-to-strategy"
              className="text-terminal-muted hover:text-terminal-text hover:underline uppercase tracking-wider font-semibold"
            >
              {t('runs.backToStrategy')} →
            </Link>
          )}
        </span>
      </div>

      {/* Stat cards — money/dates follow the active market profile (¥ on cn) */}
      <StatsGrid
        stats={run.stats}
        t={t}
        currencySymbol={profile.currency_symbol}
        locale={profile.locale}
      />

      {/* Monte Carlo distribution (runs > 1) */}
      {run.runs_summary && <RunsSummaryStrip summary={run.runs_summary} t={t} />}

      {/* Equity vs buy & hold */}
      <div className="shrink-0">
        <EquityChart
          equity={run.equity_curve}
          baseline={run.baseline_curve}
          colors={chartColors}
          baseValue={profile.seed_cash}
        />
      </div>

      {/* Trades blotter */}
      {run.trades.length > 0 && (
        <TradesBlotter
          trades={run.trades}
          t={t}
          currencySymbol={profile.currency_symbol}
          locale={profile.locale}
          lotSize={profile.lot_size}
        />
      )}
    </div>
  );
}

export default function RunPage() {
  const router = useRouter();
  const raw = router?.query?.id;
  const id = typeof raw === 'string' && raw.trim() !== '' ? raw.trim() : null;
  const t = useT();

  return (
    <AppShell>
      {id === null ? (
        <div
          data-testid="run-empty"
          className="flex items-center justify-center h-full text-terminal-muted text-xs"
        >
          {t('runs.noneSelected')}
        </div>
      ) : (
        <RunDetail id={id} />
      )}
    </AppShell>
  );
}
