/**
 * OpenOrdersTable.tsx — resting limit orders with cancel (FRONTEND_REALISM.md §3.2)
 *
 * Polls GET /api/portfolio/orders?status=open every 3s — the backend fill loop
 * executes orders in the background, so the panel keeps itself current.
 * Cancel issues DELETE /api/portfolio/orders/{id} and revalidates.
 */
import { useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/lib/fetcher';
import { formatQuantity } from '@/lib/format';
import type { OrdersResponse } from '@/types/market';

function formatTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-US', { hour12: false });
}

export default function OpenOrdersTable() {
  const { data, mutate } = useSWR<OrdersResponse>('/api/portfolio/orders?status=open', fetcher, {
    refreshInterval: 3000,
  });
  const [cancelError, setCancelError] = useState<string | null>(null);
  const orders = data?.orders;

  const handleCancel = async (id: string) => {
    setCancelError(null);
    try {
      const res = await fetch(`/api/portfolio/orders/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `Cancel failed (${res.status})`);
      }
      await mutate();
    } catch (e) {
      setCancelError(e instanceof Error ? e.message : 'Cancel failed');
      await mutate(); // the order may have filled in the meantime — refresh
    }
  };

  if (!orders || orders.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        No open orders. Place a limit order from the trade bar — it rests here until the
        price crosses your limit.
        {cancelError && (
          <p data-testid="orders-cancel-error" className="mt-1 text-terminal-down">
            {cancelError}
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <table data-testid="open-orders-table" className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-1 font-semibold">Time</th>
            <th className="text-left py-1 font-semibold">Side</th>
            <th className="text-left py-1 font-semibold">Ticker</th>
            <th className="text-right py-1 font-semibold">Qty</th>
            <th className="text-left py-1 pl-2 font-semibold">Kind</th>
            <th className="text-right py-1 font-semibold">Limit</th>
            <th className="text-right py-1 font-semibold">Stop</th>
            <th className="text-right py-1 pr-1 font-semibold" aria-label="Cancel column" />
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr
              key={o.id}
              data-testid={`open-order-row-${o.id}`}
              className="border-b border-terminal-border hover:bg-terminal-surface/50"
            >
              <td className="py-1 pl-1 tabular-nums text-terminal-muted">
                {formatTime(o.created_at)}
              </td>
              <td
                className={`py-1 font-semibold uppercase ${
                  o.side === 'buy' ? 'text-terminal-up' : 'text-terminal-down'
                }`}
              >
                {o.side}
              </td>
              <td className="py-1 font-semibold text-terminal-text">{o.ticker}</td>
              <td className="text-right py-1 tabular-nums text-terminal-text">
                {formatQuantity(o.quantity)}
              </td>
              <td className="py-1 pl-2 text-terminal-muted uppercase">
                {(o.kind ?? 'limit').replace('stop_limit', 'stp-lmt').replace('stop', 'stp').replace('limit', 'lmt')}
                {o.time_in_force === 'day' && <span className="ml-1 text-terminal-amber">day</span>}
                {o.triggered_at && <span className="ml-1 text-terminal-blue" title="Stop triggered — resting as a limit order">trig</span>}
              </td>
              <td className="text-right py-1 tabular-nums text-terminal-text">
                {o.limit_price != null
                  ? `${o.side === 'buy' ? '≤' : '≥'}$${o.limit_price.toFixed(2)}`
                  : '—'}
              </td>
              <td className="text-right py-1 tabular-nums text-terminal-text">
                {o.stop_price != null ? `@$${o.stop_price.toFixed(2)}` : '—'}
              </td>
              <td className="text-right py-1 pr-1">
                <button
                  type="button"
                  data-testid={`cancel-order-${o.id}`}
                  aria-label={`Cancel ${o.side} order for ${o.ticker}`}
                  title="Cancel order"
                  onClick={() => void handleCancel(o.id)}
                  className="text-terminal-muted hover:text-terminal-down text-sm leading-none px-1"
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {cancelError && (
        <p data-testid="orders-cancel-error" className="p-2 text-xs text-terminal-down">
          {cancelError}
        </p>
      )}
    </div>
  );
}
