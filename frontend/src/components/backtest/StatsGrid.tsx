/**
 * StatsGrid.tsx — backtest stat-card grid (P2 §8, extracted verbatim from
 * BacktestPanel as a pure refactor: DOM and testids unchanged).
 *
 * Reused by the /run and /strategy pages, so it takes the caller's bound `t`
 * rather than resolving the profile itself. Money display is likewise the
 * caller's: `currencySymbol`/`locale` default to the panel's frozen '$' /
 * 'en-US' rendering; market-aware pages pass useMarketProfile()'s values.
 */
import type { TFunction } from '@/lib/i18n';
import type { BacktestStats } from '@/types/market';
import StatCard, { signed, pnlClass } from '@/components/backtest/StatCard';

export default function StatsGrid({
  stats,
  t,
  currencySymbol = '$',
  locale = 'en-US',
}: {
  stats: BacktestStats;
  t: TFunction;
  currencySymbol?: string;
  locale?: string;
}) {
  return (
    <div className="grid grid-cols-4 lg:grid-cols-8 gap-1.5">
      <StatCard
        label={t('backtest.statReturn')}
        value={`${signed(stats.total_return_pct)}%`}
        className={pnlClass(stats.total_return_pct)}
        testid="backtest-return"
      />
      <StatCard
        label={t('backtest.statBuyHold')}
        value={`${signed(stats.buy_hold_return_pct)}%`}
        className={pnlClass(stats.buy_hold_return_pct)}
      />
      <StatCard label={t('backtest.statMaxDd')} value={`−${stats.max_drawdown_pct.toFixed(2)}%`} />
      <StatCard
        label={t('backtest.statWinRate')}
        value={stats.win_rate != null ? `${Math.round(stats.win_rate * 100)}%` : '—'}
      />
      <StatCard label={t('backtest.statEntries')} value={String(stats.fires)} />
      <StatCard label={t('backtest.statRoundTrips')} value={String(stats.round_trips)} />
      <StatCard
        label={t('backtest.statProfitFactor')}
        value={stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '—'}
      />
      <StatCard
        label={t('backtest.statFinalEquity')}
        value={`${currencySymbol}${stats.final_equity.toLocaleString(locale, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}`}
      />
    </div>
  );
}
