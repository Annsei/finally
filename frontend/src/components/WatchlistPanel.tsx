import { useState } from 'react';
import useSWR from 'swr';
import WatchlistRow from './WatchlistRow';
import type { WatchlistResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';

interface Props {
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
}

// Ticker format accepted by the backend: 1-10 uppercase letters
const TICKER_RE = /^[A-Z]{1,10}$/;

// Parse {"error": "..."} (or FastAPI {"detail": "..."}) from a failed response
async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.error ?? body?.detail ?? '';
  } catch {
    return '';
  }
}

export default function WatchlistPanel({ selectedTicker, onSelectTicker }: Props) {
  // Same SWR key as index.tsx — bound mutate revalidates every subscriber
  const { data, mutate } = useSWR<WatchlistResponse>('/api/watchlist/', fetcher);
  const tickers = data?.tickers?.map((t) => t.ticker) ?? [];

  const [addInput, setAddInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleAdd = async () => {
    const ticker = addInput.trim().toUpperCase();
    setActionError(null);

    // Client-side validation before any network call: 1-10 chars A-Z
    if (!TICKER_RE.test(ticker)) {
      setActionError('Ticker must be 1-10 letters (A-Z).');
      return;
    }
    if (tickers.includes(ticker)) {
      setActionError(`${ticker} is already in the watchlist.`);
      return;
    }

    setAdding(true);
    try {
      // POST /api/watchlist/ {ticker} — trailing slash matches the SWR key style
      const res = await fetch('/api/watchlist/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      if (!res.ok) {
        throw new Error((await readErrorDetail(res)) || `Add failed (${res.status})`);
      }
      setAddInput('');
      await mutate();
    } catch (e) {
      setActionError(e instanceof Error && e.message ? e.message : 'Failed to add ticker.');
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (ticker: string) => {
    setActionError(null);
    try {
      // DELETE /api/watchlist/{ticker} — path param, no trailing slash
      const res = await fetch(`/api/watchlist/${encodeURIComponent(ticker)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        throw new Error((await readErrorDetail(res)) || `Remove failed (${res.status})`);
      }
      await mutate();
    } catch (e) {
      setActionError(e instanceof Error && e.message ? e.message : 'Failed to remove ticker.');
    }
  };

  return (
    <div className="w-64 shrink-0 flex flex-col">
      {/* Add-ticker form — compact, dense terminal style */}
      <div className="flex gap-1 pb-2">
        <input
          type="text"
          data-testid="watchlist-add-input"
          aria-label="Add ticker"
          value={addInput}
          onChange={(e) => setAddInput(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleAdd();
          }}
          placeholder="Add ticker…"
          maxLength={10}
          disabled={adding}
          className="flex-1 min-w-0 px-2 py-1 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue placeholder:text-terminal-muted disabled:opacity-50"
        />
        <button
          type="button"
          data-testid="watchlist-add-button"
          onClick={() => void handleAdd()}
          disabled={adding || !addInput.trim()}
          className="px-2.5 py-1 rounded text-xs font-semibold text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#753991' }}
        >
          Add
        </button>
      </div>

      {/* Inline error for add/remove failures (e.g., duplicate or invalid ticker) */}
      {actionError && (
        <p data-testid="watchlist-error" className="pb-1.5 text-xs text-terminal-down leading-tight">
          {actionError}
        </p>
      )}

      {tickers.length === 0 ? (
        <div className="p-4">
          <h3 className="text-terminal-muted text-sm font-semibold">No prices yet</h3>
          <p className="text-terminal-muted text-xs mt-1">Waiting for the live market feed…</p>
        </div>
      ) : (
        <div className="overflow-y-auto min-h-0">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="text-terminal-muted border-b border-terminal-border">
                <th className="text-left py-1 pl-1 font-semibold">Symbol</th>
                <th className="text-right py-1 font-semibold">Price</th>
                <th className="text-right py-1 font-semibold">Day %</th>
                <th className="text-right py-1 pr-1 font-semibold">Chart</th>
                <th className="w-4" aria-label="Remove column" />
              </tr>
            </thead>
            <tbody>
              {tickers.map((ticker) => (
                <WatchlistRow
                  key={ticker}
                  ticker={ticker}
                  isSelected={ticker === selectedTicker}
                  onSelect={() => onSelectTicker(ticker)}
                  onRemove={() => void handleRemove(ticker)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
