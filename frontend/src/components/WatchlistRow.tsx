import { useEffect, useRef } from 'react';
import { useTicker } from '@/stores/priceStore';
import SparklineChart from './SparklineChart';
import { US_PROFILE, type MarketProfile } from '@/lib/marketProfile';

interface Props {
  ticker: string;
  isSelected: boolean;
  onSelect: () => void;
  onRemove?: () => void;
  // Optional so tests (and the US market) render exactly as before: US_PROFILE
  // has empty names and no price limits, so the name row and 涨停/跌停 badges
  // never appear. WatchlistPanel injects the live profile.
  profile?: MarketProfile;
}

// Thin session-range bar: marker shows where the live price sits between
// today's low and high (real platforms show this beside every quote)
function DayRangeBar({ low, high, price }: { low?: number; high?: number; price?: number }) {
  if (low == null || high == null || price == null || high <= low) return null;
  const pct = Math.min(100, Math.max(0, ((price - low) / (high - low)) * 100));
  return (
    <div
      data-testid="day-range-bar"
      className="mt-1 h-[3px] w-full rounded bg-terminal-border/70 relative"
      title={`Day range ${low.toFixed(2)} – ${high.toFixed(2)}`}
    >
      <div
        className="absolute top-1/2 -translate-y-1/2 h-[7px] w-[2px] rounded bg-terminal-accent"
        style={{ left: `calc(${pct}% - 1px)` }}
      />
    </div>
  );
}

export default function WatchlistRow({
  ticker,
  isSelected,
  onSelect,
  onRemove,
  profile = US_PROFILE,
}: Props) {
  const priceUpdate = useTicker(ticker);
  const name = profile.names[ticker];

  // A-share limit badges — only when the backend supplies the day's limits and
  // the live price has hit them. US quotes carry no limits, so no badge shows.
  const price = priceUpdate?.price;
  const limitUp = priceUpdate?.limit_up;
  const limitDown = priceUpdate?.limit_down;
  const atLimitUp = limitUp != null && price != null && price >= limitUp;
  const atLimitDown = limitDown != null && price != null && price <= limitDown;
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!priceUpdate || !priceRef.current) return;

    const cell = priceRef.current;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    cell.classList.remove('animate-flash-up', 'animate-flash-down');

    if (priceUpdate.direction === 'flat') return;

    void cell.offsetWidth; // force reflow so re-adding the class re-triggers the animation
    const cls = priceUpdate.direction === 'up' ? 'animate-flash-up' : 'animate-flash-down';
    cell.classList.add(cls);

    flashTimeoutRef.current = setTimeout(() => {
      cell.classList.remove(cls);
    }, 500);

    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [priceUpdate?.direction, priceUpdate?.timestamp]);

  // `group` enables the hover-reveal remove control in the trailing cell
  const rowClass = isSelected
    ? 'group border-l-2 border-terminal-accent bg-terminal-surface cursor-pointer'
    : 'group border-l-2 border-transparent cursor-pointer hover:bg-terminal-surface/50';

  // Day change vs previous close — what real platforms color quotes by.
  // Flash animation stays tick-driven; steady-state color is day-driven.
  const dayPct = priceUpdate?.day_change_percent ?? null;
  const dayColor =
    dayPct == null || dayPct === 0
      ? 'text-terminal-muted'
      : dayPct > 0
        ? 'text-terminal-up'
        : 'text-terminal-down';
  const arrow = dayPct == null || dayPct === 0 ? '' : dayPct > 0 ? '▲' : '▼';

  return (
    <tr className={rowClass} onClick={onSelect}>
      <td className="py-1 pl-1 font-semibold text-terminal-text">
        <span className="flex items-center gap-1">
          <span>{ticker}</span>
          {atLimitUp && (
            <span
              data-testid={`limit-badge-${ticker}`}
              className="text-[9px] font-semibold px-1 rounded text-terminal-up border border-terminal-up/60"
            >
              涨停
            </span>
          )}
          {atLimitDown && (
            <span
              data-testid={`limit-badge-${ticker}`}
              className="text-[9px] font-semibold px-1 rounded text-terminal-down border border-terminal-down/60"
            >
              跌停
            </span>
          )}
        </span>
        {name && (
          <span className="block text-[10px] font-normal text-terminal-muted leading-tight truncate">
            {name}
          </span>
        )}
      </td>
      <td
        ref={priceRef}
        className={`text-right py-1 tabular-nums ${dayPct == null ? 'text-terminal-text' : dayColor}`}
      >
        {priceUpdate?.price?.toFixed(2) ?? '—'}
      </td>
      <td className={`text-right py-1 tabular-nums ${dayColor}`}>
        {dayPct != null
          ? `${arrow}${dayPct > 0 ? '+' : ''}${dayPct.toFixed(2)}%`
          : '—'}
      </td>
      <td className="py-1 pr-1 w-[84px]">
        <SparklineChart ticker={ticker} width={80} height={24} />
        <DayRangeBar
          low={priceUpdate?.day_low}
          high={priceUpdate?.day_high}
          price={priceUpdate?.price}
        />
      </td>
      <td className="py-1 pr-1 w-4 text-right">
        {onRemove && (
          <button
            type="button"
            data-testid={`watchlist-remove-${ticker}`}
            aria-label={`Remove ${ticker} from watchlist`}
            title={`Remove ${ticker}`}
            onClick={(e) => {
              e.stopPropagation(); // don't trigger row selection
              onRemove();
            }}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-terminal-muted hover:text-terminal-down text-sm leading-none px-0.5 transition-opacity"
          >
            ×
          </button>
        )}
      </td>
    </tr>
  );
}
