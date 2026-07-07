/**
 * OrdersTable.tsx — trade blotter (FRONTEND_REALISM.md §1.3)
 *
 * Renders GET /api/portfolio/trades (newest first). Revalidated by index.tsx
 * after every trade — manual (TradeBar) or AI-executed (ChatPanel).
 */
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { formatQuantity } from '@/lib/format';
import type { TradesResponse } from '@/types/market';
import { useT } from '@/lib/i18n';

function formatTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-US', { hour12: false });
}

export default function OrdersTable() {
  const t = useT();
  // 10s polling — background limit-order fills appear without user action
  const { data } = useSWR<TradesResponse>('/api/portfolio/trades', fetcher, {
    refreshInterval: 10_000,
  });
  const trades = data?.trades;

  if (!trades || trades.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        {t('fills.empty')}
      </div>
    );
  }

  return (
    <table data-testid="orders-table" className="w-full text-xs border-collapse">
      <thead>
        <tr className="text-terminal-muted border-b border-terminal-border">
          <th className="text-left py-1 pl-1 font-semibold">{t('fills.colTime')}</th>
          <th className="text-left py-1 font-semibold">{t('fills.colSide')}</th>
          <th className="text-left py-1 font-semibold">{t('fills.colTicker')}</th>
          <th className="text-right py-1 font-semibold">{t('fills.colQty')}</th>
          <th className="text-right py-1 font-semibold">{t('fills.colPrice')}</th>
          <th className="text-right py-1 font-semibold">{t('fills.colValue')}</th>
          <th className="text-right py-1 font-semibold">{t('fills.colFee')}</th>
          <th className="text-right py-1 pr-1 font-semibold">{t('fills.colRealized')}</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr
            key={t.id}
            data-testid={`order-row-${t.id}`}
            className="border-b border-terminal-border hover:bg-terminal-surface/50"
          >
            <td className="py-1 pl-1 tabular-nums text-terminal-muted">
              {formatTime(t.executed_at)}
            </td>
            <td
              className={`py-1 font-semibold uppercase ${
                t.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
              }`}
            >
              {t.side}
            </td>
            <td className="py-1 font-semibold text-terminal-text">{t.ticker}</td>
            <td className="text-right py-1 tabular-nums text-terminal-text">
              {formatQuantity(t.quantity)}
            </td>
            <td className="text-right py-1 tabular-nums text-terminal-text">
              ${t.price.toFixed(2)}
            </td>
            <td className="text-right py-1 tabular-nums text-terminal-text">
              ${(t.quantity * t.price).toFixed(2)}
            </td>
            <td className="text-right py-1 tabular-nums text-terminal-muted">
              {t.commission ? `$${t.commission.toFixed(2)}` : '—'}
            </td>
            <td
              className={`text-right py-1 pr-1 tabular-nums ${
                t.realized_pnl == null
                  ? 'text-terminal-muted'
                  : t.realized_pnl >= 0
                    ? 'text-terminal-up'
                    : 'text-terminal-down'
              }`}
            >
              {t.realized_pnl != null
                ? `${t.realized_pnl >= 0 ? '+' : '-'}$${Math.abs(t.realized_pnl).toFixed(2)}`
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
