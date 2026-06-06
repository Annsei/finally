import useSWR from 'swr';
import WatchlistRow from './WatchlistRow';
import type { WatchlistResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';

interface Props {
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
}

export default function WatchlistPanel({ selectedTicker, onSelectTicker }: Props) {
  const { data } = useSWR<WatchlistResponse>('/api/watchlist', fetcher);
  const tickers = data?.tickers?.map((t) => t.ticker) ?? [];

  if (tickers.length === 0) {
    return (
      <div className="w-64 shrink-0 p-4">
        <h3 className="text-terminal-muted text-sm font-semibold">No prices yet</h3>
        <p className="text-terminal-muted text-xs mt-1">Waiting for the live market feed…</p>
      </div>
    );
  }

  return (
    <div className="w-64 shrink-0">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-terminal-muted border-b border-terminal-border">
            <th className="text-left py-1 pl-1 font-semibold">Symbol</th>
            <th className="text-right py-1 font-semibold">Price</th>
            <th className="text-right py-1 font-semibold">Change %</th>
            <th className="text-right py-1 pr-2 font-semibold">Chart</th>
          </tr>
        </thead>
        <tbody>
          {tickers.map((ticker) => (
            <WatchlistRow
              key={ticker}
              ticker={ticker}
              isSelected={ticker === selectedTicker}
              onSelect={() => onSelectTicker(ticker)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
