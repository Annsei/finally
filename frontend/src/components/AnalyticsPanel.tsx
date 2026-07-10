/**
 * AnalyticsPanel.tsx — portfolio analytics tab (PLATFORM_ROADMAP.md M3.4)
 *
 * KPI stat tiles + a sector-allocation bar list from GET /api/portfolio/analytics.
 * Sector hues are a fixed, validated categorical assignment (dataviz skill,
 * dark surface #0d1117): tech #209dd7 · financials #b8870a · crypto #a875c9;
 * cash/other are neutral remainder categories. Identity is never color-alone —
 * every row carries its label; direction/status coloring is reserved for P&L.
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { formatMoney, formatShares } from '@/lib/format';
import { useMarketProfile, type MarketProfile } from '@/lib/marketProfile';
import type { AnalyticsResponse, AnalyticsTradeRef } from '@/types/market';
import { useT, type TFunction } from '@/lib/i18n';

const SECTOR_COLORS: Record<string, string> = {
  tech: '#209dd7',
  financials: '#b8870a',
  crypto: '#a875c9',
  cash: '#8b949e',
  other: '#6e7681',
};

function StatTile({
  label,
  value,
  tone = 'neutral',
  testid,
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'up' | 'down';
  testid: string;
}) {
  const toneClass =
    tone === 'up' ? 'text-terminal-up' : tone === 'down' ? 'text-terminal-down' : 'text-terminal-text';
  return (
    <div className="px-3 py-2 rounded border border-terminal-border bg-terminal-surface/50">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted">
        {label}
      </div>
      <div data-testid={testid} className={`text-sm font-semibold tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

function tradeLine(t: TFunction, trade: AnalyticsTradeRef, profile: MarketProfile): string {
  const verb = trade.side === 'buy' ? t('analytics.buy') : t('analytics.sell');
  return `${verb} ${formatShares(trade.quantity, profile)} ${trade.ticker} @ ${formatMoney(trade.price, profile)}`;
}

export default function AnalyticsPanel() {
  const t = useT();
  const profile = useMarketProfile();
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };
  const { data } = useSWR<AnalyticsResponse>('/api/portfolio/analytics', fetcher, {
    refreshInterval: 10_000,
  });

  if (!data) {
    return <div className="p-4 text-terminal-muted text-xs">{t('analytics.loading')}</div>;
  }

  const pnlTone = data.realized_pnl > 0 ? 'up' : data.realized_pnl < 0 ? 'down' : 'neutral';
  const maxWeight = Math.max(...data.sector_allocation.map((s) => s.weight), 0.0001);

  return (
    <div className="p-3 space-y-4" data-testid="analytics-panel">
      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <StatTile label={t('analytics.trades')} value={String(data.total_trades)} testid="stat-total-trades" />
        <StatTile
          label={t('analytics.winRate')}
          value={data.win_rate != null ? `${Math.round(data.win_rate * 100)}%` : '—'}
          testid="stat-win-rate"
        />
        <StatTile
          label={t('analytics.realizedPnl')}
          value={`${data.realized_pnl > 0 ? '+' : data.realized_pnl < 0 ? '-' : ''}${formatMoney(Math.abs(data.realized_pnl), money)}`}
          tone={pnlTone}
          testid="stat-realized"
        />
        <StatTile
          label={t('analytics.maxDrawdown')}
          value={data.max_drawdown_pct != null ? `${data.max_drawdown_pct.toFixed(1)}%` : '—'}
          testid="stat-drawdown"
        />
        <StatTile
          label={t('analytics.sharpe')}
          value={data.sharpe != null ? data.sharpe.toFixed(2) : '—'}
          testid="stat-sharpe"
        />
      </div>

      {/* Sector allocation — labeled bar list, weights sum to portfolio value */}
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted mb-1.5">
          {t('analytics.allocation')}
        </div>
        <div className="space-y-1.5" data-testid="sector-allocation">
          {data.sector_allocation.map((s) => (
            <div key={s.sector} className="flex items-center gap-2 text-xs">
              <span className="w-20 shrink-0 text-terminal-muted capitalize">{s.sector}</span>
              <span className="flex-1 h-2 rounded-sm bg-terminal-border/30 overflow-hidden">
                <span
                  className="block h-full rounded-sm"
                  title={formatMoney(s.value, money)}
                  style={{
                    width: `${Math.max(1, (s.weight / maxWeight) * 100)}%`,
                    backgroundColor: SECTOR_COLORS[s.sector] ?? SECTOR_COLORS.other,
                  }}
                />
              </span>
              <span className="w-12 shrink-0 text-right tabular-nums text-terminal-text">
                {(s.weight * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Best / worst closed trades */}
      {(data.best_trade || data.worst_trade) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          {data.best_trade && (
            <div className="px-3 py-2 rounded border border-terminal-border bg-terminal-surface/50">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted block">
                {t('analytics.bestTrade')}
              </span>
              <span data-testid="best-trade" className="text-terminal-text">
                {tradeLine(t, data.best_trade, profile)}{' '}
                <span className="text-terminal-up tabular-nums">
                  +{formatMoney(data.best_trade.realized_pnl, money)}
                </span>
              </span>
            </div>
          )}
          {data.worst_trade && (
            <div className="px-3 py-2 rounded border border-terminal-border bg-terminal-surface/50">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-terminal-muted block">
                {t('analytics.worstTrade')}
              </span>
              <span data-testid="worst-trade" className="text-terminal-text">
                {tradeLine(t, data.worst_trade, profile)}{' '}
                <span
                  className={`tabular-nums ${data.worst_trade.realized_pnl < 0 ? 'text-terminal-down' : 'text-terminal-up'}`}
                >
                  {data.worst_trade.realized_pnl < 0 ? '-' : '+'}
                  {formatMoney(Math.abs(data.worst_trade.realized_pnl), money)}
                </span>
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
