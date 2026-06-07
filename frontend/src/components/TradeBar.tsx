/**
 * TradeBar.tsx — Manual trade entry form (FE-13)
 *
 * Inputs:
 *   - Ticker text input (auto-filled from selectedTicker prop via useEffect)
 *   - Qty number input
 *   - Buy button (green #22c55e)
 *   - Sell button (red #ef4444)
 *
 * Trade execution:
 *   - Client-side validation before any network call (T-4-01, T-4-03)
 *   - POST /api/portfolio/trade with optimistic SWR mutate
 *   - Inline error display below inputs (D-14)
 *
 * SWR key '/api/portfolio' — exact match with Header.tsx so a trade
 * revalidates the header cash/portfolio total at the same time.
 */
'use client';

import { useState, useEffect } from 'react';
import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
import { usePriceStore } from '@/stores/priceStore';

interface TradeBarProps {
  selectedTicker: string | null;
  onTradeComplete?: () => void;
}

export default function TradeBar({ selectedTicker, onTradeComplete }: TradeBarProps) {
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Shared SWR key with Header.tsx — trade revalidates header too
  const { data: portfolio, mutate } = useSWR<PortfolioResponse>('/api/portfolio', fetcher);

  // Auto-fill ticker from parent selection (D-12)
  useEffect(() => {
    if (selectedTicker) setTicker(selectedTicker);
  }, [selectedTicker]);

  const handleTrade = async (side: 'buy' | 'sell') => {
    // Clear prior error on each new attempt (D-14)
    setError(null);

    // Client-side validation BEFORE any network call (T-4-01, T-4-03)
    const trimmedTicker = ticker.trim().toUpperCase();
    if (!trimmedTicker || !/^[A-Z]+$/.test(trimmedTicker)) {
      setError('Enter a valid ticker and quantity.');
      return;
    }
    if (!isFinite(Number(qty)) || Number(qty) <= 0) {
      setError('Enter a valid ticker and quantity.');
      return;
    }

    setPending(true);
    try {
      await mutate(
        async (current) => {
          const res = await fetch('/api/portfolio/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ticker: trimmedTicker,
              quantity: Number(qty),
              side,
            }),
          });
          if (!res.ok) {
            const data = await res.json();
            throw new Error(data.error ?? 'Trade failed');
          }
          // Return current; revalidate: true will fetch the real updated state
          return current;
        },
        {
          optimisticData: (current) => {
            if (!current) return current;
            // Look up price from current positions or fall back to Zustand price store
            const price =
              current.positions.find((p) => p.ticker === trimmedTicker)?.current_price ??
              usePriceStore.getState().prices[trimmedTicker]?.price ??
              0;
            const cost = Number(qty) * price;
            return {
              ...current,
              cash: current.cash + (side === 'sell' ? cost : -cost),
            };
          },
          rollbackOnError: true,
          revalidate: true,
        }
      );
      onTradeComplete?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trade failed');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="p-3 bg-terminal-surface border-t border-terminal-border">
      <div className="flex items-end gap-2">
        {/* Ticker input */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor="trade-ticker"
            className="text-xs font-semibold text-terminal-muted uppercase tracking-wider"
          >
            Ticker
          </label>
          <input
            id="trade-ticker"
            aria-label="Ticker"
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            placeholder="AAPL"
            disabled={pending}
            className="w-24 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50"
          />
        </div>

        {/* Qty input */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor="trade-qty"
            className="text-xs font-semibold text-terminal-muted uppercase tracking-wider"
          >
            Qty
          </label>
          <input
            id="trade-qty"
            aria-label="Qty"
            type="number"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder="0"
            min="0"
            step="any"
            disabled={pending}
            className="w-20 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50"
          />
        </div>

        {/* Buy button — green semantic (profit/long direction) */}
        <button
          onClick={() => handleTrade('buy')}
          disabled={pending}
          className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#22c55e' }}
        >
          Buy
        </button>

        {/* Sell button — red semantic (loss/short direction) */}
        <button
          onClick={() => handleTrade('sell')}
          disabled={pending}
          className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#ef4444' }}
        >
          Sell
        </button>
      </div>

      {/* Inline error display — 12px, red, below inputs (D-14) */}
      {error && (
        <p className="mt-1.5 text-xs text-terminal-down leading-tight">{error}</p>
      )}
    </div>
  );
}
