import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';

export default function PortfolioHeatmap() {
  const { data } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);
  const positions = data?.positions;
  const totalValue = data?.total_value ?? 0;

  if (!positions || positions.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        No positions yet. Use the trade bar to buy shares.
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-1 p-2 bg-terminal-surface rounded">
      {positions.map((pos) => {
        const posValue = pos.quantity * pos.current_price;
        const widthPct = (posValue / totalValue) * 100;
        const alpha = Math.min(Math.abs(pos.pnl_pct) / 20, 1.0);
        const bg =
          pos.pnl_pct > 0
            ? `rgba(34, 197, 94, ${Math.max(alpha, 0.3)})`
            : pos.pnl_pct < 0
              ? `rgba(239, 68, 68, ${Math.max(alpha, 0.3)})`
              : '#1a1a2e';

        return (
          <div
            key={pos.ticker}
            style={{ width: `${widthPct}%`, minWidth: '64px', backgroundColor: bg }}
            className="p-2 text-terminal-text rounded text-xs"
          >
            <div className="font-semibold">{pos.ticker}</div>
            <div className="tabular-nums">${posValue.toFixed(0)}</div>
            <div
              className={`tabular-nums ${
                pos.pnl_pct > 0
                  ? 'text-terminal-up'
                  : pos.pnl_pct < 0
                    ? 'text-terminal-down'
                    : 'text-terminal-muted'
              }`}
            >
              {pos.pnl_pct > 0 ? '+' : ''}
              {pos.pnl_pct.toFixed(2)}%
            </div>
          </div>
        );
      })}
    </div>
  );
}
