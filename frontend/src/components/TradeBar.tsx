/**
 * TradeBar.tsx — Manual trade entry form (FE-13, Batch-1 realism upgrade)
 *
 * Inputs:
 *   - Ticker text input (auto-filled from selectedTicker prop via useEffect)
 *   - Qty number input
 *   - Buy button (green #22c55e)
 *   - Sell button (red #ef4444)
 *
 * Realism affordances (FRONTEND_REALISM.md §1.2):
 *   - Live estimated notional (qty × live price) while typing
 *   - Max-buyable (cash ÷ price) and held quantity, clickable to fill qty
 *   - Fill toast ("Bought 5 AAPL @ $190.02") after execution
 *
 * Trade execution:
 *   - Client-side validation before any network call (T-4-01, T-4-03)
 *   - POST /api/portfolio/trade with optimistic SWR mutate
 *   - Inline error display below inputs (D-14)
 *
 * SWR key '/api/portfolio/' — exact match with Header.tsx so a trade
 * revalidates the header cash/portfolio total at the same time.
 */
'use client';

import { useState, useEffect, useRef } from 'react';
import useSWR from 'swr';
import type { PortfolioResponse } from '@/types/market';
import { fetcher } from '@/lib/fetcher';
import { formatQuantity } from '@/lib/format';
import { usePriceStore, useTicker } from '@/stores/priceStore';

interface TradeBarProps {
  selectedTicker: string | null;
  onTradeComplete?: () => void;
}

interface TradeFill {
  ticker: string;
  side: string;
  quantity: number;
  price: number;
}

export default function TradeBar({ selectedTicker, onTradeComplete }: TradeBarProps) {
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Shared SWR key with Header.tsx — trade revalidates header too
  const { data: portfolio, mutate } = useSWR<PortfolioResponse>('/api/portfolio/', fetcher);

  // Auto-fill ticker from parent selection (D-12)
  useEffect(() => {
    if (selectedTicker) setTicker(selectedTicker);
  }, [selectedTicker]);

  // Auto-dismiss the fill toast
  useEffect(() => {
    if (!toast) return;
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, [toast]);

  // Live figures for the estimate row
  const normalizedTicker = ticker.trim().toUpperCase();
  const liveUpdate = useTicker(normalizedTicker);
  const position = portfolio?.positions.find((p) => p.ticker === normalizedTicker);
  const livePrice = liveUpdate?.price ?? position?.current_price ?? null;
  const qtyNum = Number(qty);
  const qtyValid = isFinite(qtyNum) && qtyNum > 0;
  const estimate = livePrice != null && qtyValid ? qtyNum * livePrice : null;
  const maxBuy =
    livePrice != null && livePrice > 0 && portfolio
      ? Math.floor((portfolio.cash / livePrice) * 10000) / 10000
      : null;
  const held = position?.quantity ?? null;

  const handleTrade = async (side: 'buy' | 'sell') => {
    // Clear prior error on each new attempt (D-14)
    setError(null);

    // Client-side validation BEFORE any network call (T-4-01, T-4-03)
    const trimmedTicker = normalizedTicker;
    if (!trimmedTicker || !/^[A-Z]+$/.test(trimmedTicker)) {
      setError('Enter a valid ticker and quantity.');
      return;
    }
    if (!qtyValid) {
      setError('Enter a valid ticker and quantity.');
      return;
    }

    setPending(true);
    let fill: TradeFill | null = null;
    try {
      await mutate(
        async () => {
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
          fill = (await res.json()) as TradeFill;
          // Return a FRESH snapshot: the committed cache passed in here is the
          // pre-optimistic value, so returning it would revert the optimistic
          // update and flash stale cash until revalidation.
          return await fetcher('/api/portfolio/');
        },
        {
          optimisticData: portfolio
            ? (current) => {
                const base = current ?? portfolio;
                // Look up price from current positions or fall back to Zustand price store
                const price =
                  base.positions.find((p) => p.ticker === trimmedTicker)?.current_price ??
                  usePriceStore.getState().prices[trimmedTicker]?.price ??
                  0;
                const cost = Number(qty) * price;
                return {
                  ...base,
                  cash: base.cash + (side === 'sell' ? cost : -cost),
                };
              }
            : undefined,
          rollbackOnError: true,
          // The mutator already returns a fresh post-trade snapshot
          revalidate: false,
        }
      );
      if (fill) {
        const f: TradeFill = fill;
        setToast(
          `${f.side === 'buy' ? 'Bought' : 'Sold'} ${formatQuantity(f.quantity)} ${f.ticker} @ $${f.price.toFixed(2)}`
        );
      }
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

        {/* Live estimate row — notional preview, max-buy and held shortcuts */}
        <div
          data-testid="trade-estimate"
          className="flex items-baseline gap-3 pb-1.5 text-xs text-terminal-muted tabular-nums"
        >
          <span>
            Est.{' '}
            <span className="text-terminal-text">
              {estimate != null
                ? `$${estimate.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : '—'}
            </span>
          </span>
          {maxBuy != null && (
            <button
              type="button"
              data-testid="trade-max-buy"
              title="Set quantity to the maximum buyable with current cash"
              onClick={() => setQty(String(maxBuy))}
              className="hover:text-terminal-blue transition-colors"
            >
              Max buy <span className="text-terminal-text">{formatQuantity(maxBuy)}</span>
            </button>
          )}
          {held != null && held > 0 && (
            <button
              type="button"
              data-testid="trade-held"
              title="Set quantity to your full position"
              onClick={() => setQty(String(held))}
              className="hover:text-terminal-blue transition-colors"
            >
              Held <span className="text-terminal-text">{formatQuantity(held)}</span>
            </button>
          )}
        </div>
      </div>

      {/* Inline error display — 12px, red, below inputs (D-14) */}
      {error && (
        <p className="mt-1.5 text-xs text-terminal-down leading-tight">{error}</p>
      )}

      {/* Fill toast — bottom-right, auto-dismisses */}
      {toast && (
        <div
          data-testid="trade-toast"
          className="fixed bottom-4 right-4 z-50 px-3 py-2 rounded text-xs bg-terminal-surface text-terminal-text shadow-lg"
          style={{ border: '1px solid #22c55e' }}
        >
          ✓ {toast}
        </div>
      )}
    </div>
  );
}
