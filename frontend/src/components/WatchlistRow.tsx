import { useEffect, useRef } from 'react';
import { useTicker } from '@/stores/priceStore';
import SparklineChart from './SparklineChart';

interface Props {
  ticker: string;
  isSelected: boolean;
  onSelect: () => void;
}

export default function WatchlistRow({ ticker, isSelected, onSelect }: Props) {
  const priceUpdate = useTicker(ticker);
  const priceRef = useRef<HTMLTableCellElement>(null);
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Flash animation: clear prior timeout, force reflow, re-add class (Pitfall 5)
  useEffect(() => {
    if (!priceUpdate || !priceRef.current) return;
    if (priceUpdate.direction === 'flat') return;

    const cell = priceRef.current;
    if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);

    const cls = priceUpdate.direction === 'up' ? 'flash-up' : 'flash-down';
    cell.classList.remove('flash-up', 'flash-down');
    void cell.offsetWidth; // force reflow so re-adding the class re-triggers transition
    cell.classList.add(cls);

    flashTimeoutRef.current = setTimeout(() => {
      cell.classList.remove(cls);
    }, 500);

    return () => {
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
    };
  }, [priceUpdate?.direction, priceUpdate?.timestamp]);

  const rowClass = isSelected
    ? 'border-l-2 border-terminal-accent bg-terminal-surface cursor-pointer'
    : 'border-l-2 border-transparent cursor-pointer hover:bg-terminal-surface/50';

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
      <td className="py-1 pr-2">
        <SparklineChart ticker={ticker} width={80} height={28} />
      </td>
    </tr>
  );
}
