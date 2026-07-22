import { useEffect, useRef } from 'react';
import useSWR from 'swr';
import type { PortfolioResponse, Position } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
import { useTicker } from '@/stores/priceStore';
import { formatMoney, formatShares } from '@/lib/format';
import { useMarketProfile, type MarketProfile } from '@/lib/marketProfile';
import SymbolLink from '@/components/SymbolLink';
import { useT } from '@/lib/i18n';

// Inner component: one row per position with live price + flash animation
function PositionsRow({ pos, profile }: { pos: Position; profile: MarketProfile }) {
  const priceUpdate = useTicker(pos.ticker);
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Flash animation on current-price cell — mirrors WatchlistRow lifecycle
  useEffect(() => {
    if (!priceUpdate || !priceRef.current) return;
    if (priceUpdate.direction === 'flat') return;

    const cell = priceRef.current;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);

    const cls = priceUpdate.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
    cell.classList.remove('animate-flash-up', 'animate-flash-down');
    void cell.offsetWidth; // force reflow so re-adding the class re-triggers the animation
    cell.classList.add(cls);

    flashTimeoutRef.current = setTimeout(() => {
      cell.classList.remove(cls);
    }, 500);

    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [priceUpdate]);

  // Live values: use Zustand price if available, fall back to SWR portfolio data
  const currentPrice = priceUpdate?.price ?? pos.current_price;
  const liveUnrealizedPnl = (currentPrice - pos.avg_cost) * pos.quantity;
  const livePnlPct =
    pos.avg_cost > 0 ? ((currentPrice - pos.avg_cost) / pos.avg_cost) * 100 : 0;

  const pnlColor =
    liveUnrealizedPnl > 0
      ? 'text-terminal-up'
      : liveUnrealizedPnl < 0
        ? 'text-terminal-down'
        : 'text-terminal-muted';

  const pctColor =
    livePnlPct > 0
      ? 'text-terminal-up'
      : livePnlPct < 0
        ? 'text-terminal-down'
        : 'text-terminal-muted';
  const money = { currency_symbol: profile.currency_symbol, locale: profile.locale };

  return (
    <tr
      data-testid={`position-row-${pos.ticker}`}
      className="border-b border-terminal-border hover:bg-terminal-surface/50"
    >
      <td className="py-1 pl-1 font-semibold text-terminal-text tabular-nums">
        <SymbolLink code={pos.ticker} />
      </td>
      <td className="text-right py-1 tabular-nums text-terminal-text">
        {formatShares(pos.quantity, profile)}
      </td>
      <td className="text-right py-1 tabular-nums text-terminal-text">
        {formatMoney(pos.avg_cost, money)}
      </td>
      <td
        ref={priceRef}
        data-price-cell={pos.ticker}
        className="text-right py-1 tabular-nums text-terminal-text"
      >
        {formatMoney(currentPrice, money)}
      </td>
      <td className={`text-right py-1 tabular-nums ${pnlColor}`}>
        {liveUnrealizedPnl >= 0 ? '+' : '-'}
        {formatMoney(Math.abs(liveUnrealizedPnl), money)}
      </td>
      <td className={`text-right py-1 pr-1 tabular-nums ${pctColor}`}>
        {livePnlPct >= 0 ? '+' : ''}
        {livePnlPct.toFixed(2)}%
      </td>
    </tr>
  );
}

export default function PositionsTable() {
  const t = useT();
  const profile = useMarketProfile();
  const { data } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);
  const positions = data?.positions;

  if (!positions || positions.length === 0) {
    return (
      <div className="p-4 text-terminal-muted text-xs">
        {t('positions.empty')}
      </div>
    );
  }

  return (
    <table data-testid="positions-table" className="w-full text-xs border-collapse">
      <thead>
        <tr className="text-terminal-muted border-b border-terminal-border">
          <th className="text-left py-1 pl-1 font-semibold">{t('positions.colTicker')}</th>
          <th className="text-right py-1 font-semibold">{t('positions.colQty')}</th>
          <th className="text-right py-1 font-semibold">{t('positions.colAvgCost')}</th>
          <th className="text-right py-1 font-semibold">{t('positions.colPrice')}</th>
          <th className="text-right py-1 font-semibold">{t('positions.colPnl')}</th>
          <th className="text-right py-1 pr-1 font-semibold">{t('positions.colChange')}</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((pos) => (
          <PositionsRow key={pos.ticker} pos={pos} profile={profile} />
        ))}
      </tbody>
    </table>
  );
}
