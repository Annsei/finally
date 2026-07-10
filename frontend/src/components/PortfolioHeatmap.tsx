import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
import { useMarketProfile } from '@/lib/marketProfile';
import { useT } from '@/lib/i18n';

export default function PortfolioHeatmap() {
  const t = useT();
  const profile = useMarketProfile();
  const { data } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);
  const positions = data?.positions;
  const totalValue = data?.total_value ?? 0;

  // Direction RGB flips with the market: profit tiles use the "up" colour,
  // losses the "down" colour. US → profit green / loss red (unchanged).
  const GREEN = '34, 197, 94';
  const RED = '239, 68, 68';
  const upRgb = profile.up_is_red ? RED : GREEN;
  const downRgb = profile.up_is_red ? GREEN : RED;

  if (!positions || positions.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        {t('positions.empty')}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-1 p-2 bg-terminal-surface rounded">
      {positions.map((pos) => {
        const posValue = pos.quantity * pos.current_price;
        // Guard divide-by-zero: total_value of 0 would yield NaN widths
        const widthPct = totalValue > 0 ? (posValue / totalValue) * 100 : 0;
        const alpha = Math.min(Math.abs(pos.pnl_pct) / 20, 1.0);
        const bg =
          pos.pnl_pct > 0
            ? `rgba(${upRgb}, ${Math.max(alpha, 0.3)})`
            : pos.pnl_pct < 0
              ? `rgba(${downRgb}, ${Math.max(alpha, 0.3)})`
              : '#1a1a2e';

        return (
          <div
            key={pos.ticker}
            style={{ width: `${widthPct}%`, minWidth: '64px', backgroundColor: bg }}
            className="p-2 text-terminal-text rounded text-xs"
          >
            <div className="font-semibold">{pos.ticker}</div>
            <div className="tabular-nums">{profile.currency_symbol}{posValue.toFixed(0)}</div>
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
