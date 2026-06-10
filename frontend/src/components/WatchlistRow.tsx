import { useEffect, useRef } from 'react';
import { useTicker } from '@/stores/priceStore';
import SparklineChart from './SparklineChart';

interface Props {
  ticker: string;
  isSelected: boolean;
  onSelect: () => void;
  onRemove?: () => void;
}

export default function WatchlistRow({ ticker, isSelected, onSelect, onRemove }: Props) {
  const priceUpdate = useTicker(ticker);
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

  const changeColor =
    priceUpdate?.direction === 'up'
      ? 'text-terminal-up'
      : priceUpdate?.direction === 'down'
        ? 'text-terminal-down'
        : 'text-terminal-muted';

  return (
    <tr className={rowClass} onClick={onSelect}>
      <td className="py-1 pl-1 font-semibold text-terminal-text">{ticker}</td>
      <td ref={priceRef} className="text-right py-1 tabular-nums">
        {priceUpdate?.price?.toFixed(2) ?? '—'}
      </td>
      <td className={`text-right py-1 tabular-nums ${changeColor}`}>
        {priceUpdate?.change_percent != null
          ? `${priceUpdate.change_percent > 0 ? '+' : ''}${priceUpdate.change_percent.toFixed(2)}%`
          : '—'}
      </td>
      <td className="py-1 pr-1">
        <SparklineChart ticker={ticker} width={80} height={28} />
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
