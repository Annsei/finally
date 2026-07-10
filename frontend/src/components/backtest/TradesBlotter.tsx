/**
 * TradesBlotter.tsx — backtest trade-by-trade table (P2 §8, extracted verbatim
 * from BacktestPanel as a pure refactor: DOM and testids unchanged). The
 * caller decides whether trades exist before mounting. Money/date display is
 * the caller's: every consumer must pass active market profile values.
 */
import type { TFunction } from '@/lib/i18n';
import type { BacktestTrade, BacktestTradeReason } from '@/types/market';
import { formatShares } from '@/lib/format';
import { pnlClass } from '@/components/backtest/StatCard';

const REASON_KEY: Record<BacktestTradeReason, string> = {
  trigger: 'backtest.reason.trigger',
  take_profit: 'backtest.reason.take_profit',
  stop_loss: 'backtest.reason.stop_loss',
  horizon_end: 'backtest.reason.horizon_end',
};

export default function TradesBlotter({
  trades,
  t,
  currencySymbol,
  locale,
  lotSize,
}: {
  trades: BacktestTrade[];
  t: TFunction;
  currencySymbol: string;
  locale: string;
  lotSize: number;
}) {
  return (
    <div className="mt-2 max-h-40 overflow-y-auto">
      <table data-testid="backtest-trades" className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-1 font-semibold">{t('backtest.colTime')}</th>
            <th className="text-left py-1 font-semibold">{t('backtest.colSide')}</th>
            <th className="text-right py-1 font-semibold">{t('backtest.colQty')}</th>
            <th className="text-right py-1 font-semibold">{t('backtest.colPrice')}</th>
            <th className="text-left py-1 pl-3 font-semibold">{t('backtest.colReason')}</th>
            <th className="text-right py-1 pr-1 font-semibold">{t('backtest.colPnl')}</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((tr, i) => (
            <tr key={i} className="border-b border-terminal-border">
              <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                {new Date(tr.time * 1000).toLocaleDateString(locale, {
                  month: 'short',
                  day: 'numeric',
                })}
              </td>
              <td
                className={`py-1 font-semibold uppercase ${
                  tr.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                }`}
              >
                {locale.toLowerCase().startsWith('zh')
                  ? tr.side === 'buy'
                    ? t('analytics.buy')
                    : t('analytics.sell')
                  : tr.side}
              </td>
              <td className="text-right py-1 tabular-nums">
                {formatShares(tr.quantity, { lot_size: lotSize })}
              </td>
              <td className="text-right py-1 tabular-nums">{`${currencySymbol}${tr.price.toFixed(2)}`}</td>
              <td className="py-1 pl-3 text-terminal-muted">{t(REASON_KEY[tr.reason])}</td>
              <td
                className={`text-right py-1 pr-1 tabular-nums ${
                  tr.pnl != null ? pnlClass(tr.pnl) : 'text-terminal-muted'
                }`}
              >
                {tr.pnl != null
                  ? `${tr.pnl >= 0 ? '+' : '-'}${currencySymbol}${Math.abs(tr.pnl).toFixed(2)}`
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
