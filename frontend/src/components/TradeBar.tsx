/**
 * TradeBar.tsx — order ticket (FE-13; realism Batches 1-3 + PLATFORM_ROADMAP M1)
 *
 * Order kinds (M1):
 *   - Market: POST /api/portfolio/trade — instant fill at ask (buy) / bid (sell)
 *   - Limit:  rests until the quote crosses the limit (marketable fills now)
 *   - Stop:   market-on-trigger — buy arms above the ask, sell below the bid
 *   - Stop-limit: on trigger converts to a resting limit order
 *   Non-market orders carry a time-in-force (GTC default, DAY expires in 24h).
 *
 * Risk rail (M1.5): a non-blocking concentration warning appears when the
 * prospective buy would push a single position past 40% of portfolio value.
 *
 * SWR key '/api/portfolio/' — exact match with Header.tsx so a trade
 * revalidates the header cash/portfolio total at the same time.
 */
'use client';

import { useState, useEffect, useRef } from 'react';
import useSWR from 'swr';
import type { PortfolioResponse, OrderPostResponse, OrderKind, TimeInForce } from '@/types/market';
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

type OrderType = 'market' | OrderKind;

const CONCENTRATION_WARN = 0.4;

const ORDER_TYPES: { key: OrderType; label: string; testid: string }[] = [
  { key: 'market', label: 'Mkt', testid: 'order-type-market' },
  { key: 'limit', label: 'Lmt', testid: 'order-type-limit' },
  { key: 'stop', label: 'Stp', testid: 'order-type-stop' },
  { key: 'stop_limit', label: 'StpLmt', testid: 'order-type-stop-limit' },
];

export default function TradeBar({ selectedTicker, onTradeComplete }: TradeBarProps) {
  const [orderType, setOrderType] = useState<OrderType>('market');
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');
  const [limitPrice, setLimitPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [tif, setTif] = useState<TimeInForce>('gtc');
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

  const needsLimit = orderType === 'limit' || orderType === 'stop_limit';
  const needsStop = orderType === 'stop' || orderType === 'stop_limit';

  // Live figures for the estimate row
  const normalizedTicker = ticker.trim().toUpperCase();
  const liveUpdate = useTicker(normalizedTicker);
  const position = portfolio?.positions.find((p) => p.ticker === normalizedTicker);
  const livePrice = liveUpdate?.price ?? position?.current_price ?? null;
  const qtyNum = Number(qty);
  const qtyValid = isFinite(qtyNum) && qtyNum > 0;
  const limitNum = Number(limitPrice);
  const limitValid = isFinite(limitNum) && limitNum > 0;
  const stopNum = Number(stopPrice);
  const stopValid = isFinite(stopNum) && stopNum > 0;

  // Cost-math basis: the limit when set, the stop for pure stops, else live
  const basisPrice = needsLimit && limitValid ? limitNum : needsStop && stopValid ? stopNum : livePrice;
  const estimate = basisPrice != null && qtyValid ? qtyNum * basisPrice : null;
  // Buys fill at the ask — size the max-buy shortcut against it (or the basis)
  const askPrice =
    orderType !== 'market' && basisPrice != null ? basisPrice : (liveUpdate?.ask ?? livePrice);
  const maxBuy =
    askPrice != null && askPrice > 0 && portfolio
      ? Math.floor((portfolio.cash / askPrice) * 10000) / 10000
      : null;
  const held = position?.quantity ?? null;

  // Concentration rail (M1.5): prospective post-buy weight of this ticker
  const totalValue = portfolio?.total_value ?? 0;
  const prospectiveWeight =
    qtyValid && basisPrice != null && totalValue > 0
      ? (((held ?? 0) + qtyNum) * basisPrice) / totalValue
      : null;
  const concentrated = prospectiveWeight != null && prospectiveWeight > CONCENTRATION_WARN;

  const validate = (): boolean => {
    if (!normalizedTicker || !/^[A-Z]+$/.test(normalizedTicker)) {
      setError('Enter a valid ticker and quantity.');
      return false;
    }
    if (!qtyValid) {
      setError('Enter a valid ticker and quantity.');
      return false;
    }
    if (needsLimit && !limitValid) {
      setError('Enter a valid limit price.');
      return false;
    }
    if (needsStop && !stopValid) {
      setError('Enter a valid stop price.');
      return false;
    }
    return true;
  };

  const placeOrder = async (side: 'buy' | 'sell') => {
    const res = await fetch('/api/portfolio/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: normalizedTicker,
        quantity: qtyNum,
        side,
        kind: orderType as OrderKind,
        limit_price: needsLimit ? limitNum : undefined,
        stop_price: needsStop ? stopNum : undefined,
        time_in_force: tif,
      }),
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error ?? 'Order failed');
    }
    const { order } = (await res.json()) as OrderPostResponse;
    const verb = side === 'buy' ? 'Buy' : 'Sell';
    if (order.status === 'filled' && order.fill_price != null) {
      setToast(
        `${side === 'buy' ? 'Bought' : 'Sold'} ${formatQuantity(order.quantity)} ${order.ticker} @ $${order.fill_price.toFixed(2)}`
      );
      await mutate(); // filled immediately — refresh cash/positions
    } else if (order.kind === 'stop') {
      setToast(
        `Stop placed: ${verb} ${formatQuantity(order.quantity)} ${order.ticker} @ stop $${order.stop_price?.toFixed(2)}`
      );
    } else if (order.kind === 'stop_limit') {
      setToast(
        `Stop-limit placed: ${verb} ${formatQuantity(order.quantity)} ${order.ticker} @ stop $${order.stop_price?.toFixed(2)} / ${side === 'buy' ? '≤' : '≥'}$${order.limit_price?.toFixed(2)}`
      );
    } else {
      setToast(
        `Order placed: ${verb} ${formatQuantity(order.quantity)} ${order.ticker} @ ${side === 'buy' ? '≤' : '≥'}$${order.limit_price?.toFixed(2)}`
      );
    }
  };

  const executeMarketOrder = async (side: 'buy' | 'sell') => {
    let fill: TradeFill | null = null;
    await mutate(
      async () => {
        const res = await fetch('/api/portfolio/trade', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ticker: normalizedTicker,
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
                base.positions.find((p) => p.ticker === normalizedTicker)?.current_price ??
                usePriceStore.getState().prices[normalizedTicker]?.price ??
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
  };

  const handleTrade = async (side: 'buy' | 'sell') => {
    // Clear prior error on each new attempt (D-14)
    setError(null);

    // Client-side validation BEFORE any network call (T-4-01, T-4-03)
    if (!validate()) return;

    setPending(true);
    try {
      if (orderType === 'market') {
        await executeMarketOrder(side);
      } else {
        await placeOrder(side);
      }
      onTradeComplete?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trade failed');
    } finally {
      setPending(false);
    }
  };

  const typeButtonClass = (t: OrderType) =>
    `px-2 py-1.5 text-xs font-semibold rounded transition-colors ${
      orderType === t
        ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
        : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
    }`;

  const tifButtonClass = (t: TimeInForce) =>
    `px-1.5 py-0.5 rounded text-[10px] font-semibold transition-colors ${
      tif === t
        ? 'bg-terminal-bg text-terminal-text border border-terminal-blue'
        : 'text-terminal-muted border border-terminal-border hover:text-terminal-text'
    }`;

  return (
    <div className="p-3 bg-terminal-surface border-t border-terminal-border">
      <div className="flex items-end gap-2 flex-wrap">
        {/* Order kind — market / limit / stop / stop-limit */}
        <div className="flex flex-col gap-1">
          <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
            Type
          </span>
          <div className="flex gap-1">
            {ORDER_TYPES.map((t) => (
              <button
                key={t.key}
                type="button"
                data-testid={t.testid}
                onClick={() => setOrderType(t.key)}
                disabled={pending}
                className={typeButtonClass(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

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
            list="ticker-suggestions"
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

        {/* Stop price — stop and stop-limit */}
        {needsStop && (
          <div className="flex flex-col gap-1">
            <label
              htmlFor="trade-stop-price"
              className="text-xs font-semibold text-terminal-muted uppercase tracking-wider"
            >
              Stop $
            </label>
            <input
              id="trade-stop-price"
              aria-label="Stop price"
              type="number"
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
              placeholder="0.00"
              min="0"
              step="any"
              disabled={pending}
              className="w-24 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50"
            />
          </div>
        )}

        {/* Limit price — limit and stop-limit */}
        {needsLimit && (
          <div className="flex flex-col gap-1">
            <label
              htmlFor="trade-limit-price"
              className="text-xs font-semibold text-terminal-muted uppercase tracking-wider"
            >
              Limit $
            </label>
            <input
              id="trade-limit-price"
              aria-label="Limit price"
              type="number"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="0.00"
              min="0"
              step="any"
              disabled={pending}
              className="w-24 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border text-terminal-text rounded focus:outline-none focus:border-terminal-blue tabular-nums placeholder:text-terminal-muted disabled:opacity-50"
            />
          </div>
        )}

        {/* Time-in-force — resting orders only */}
        {orderType !== 'market' && (
          <div className="flex flex-col gap-1 pb-0.5">
            <span className="text-xs font-semibold text-terminal-muted uppercase tracking-wider">
              TIF
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                data-testid="tif-gtc"
                onClick={() => setTif('gtc')}
                disabled={pending}
                className={tifButtonClass('gtc')}
              >
                GTC
              </button>
              <button
                type="button"
                data-testid="tif-day"
                onClick={() => setTif('day')}
                disabled={pending}
                className={tifButtonClass('day')}
              >
                DAY
              </button>
            </div>
          </div>
        )}

        {/* Buy button — green semantic (profit/long direction) */}
        <button
          data-testid="trade-buy-button"
          onClick={() => handleTrade('buy')}
          disabled={pending}
          className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#22c55e' }}
        >
          Buy
        </button>

        {/* Sell button — red semantic (loss/short direction) */}
        <button
          data-testid="trade-sell-button"
          onClick={() => handleTrade('sell')}
          disabled={pending}
          className="px-4 py-1.5 text-xs font-semibold rounded min-h-[36px] text-white disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
          style={{ backgroundColor: '#ef4444' }}
        >
          Sell
        </button>

        {/* Live estimate row — notional preview, quote, max-buy and held shortcuts */}
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
          {liveUpdate?.bid != null && liveUpdate?.ask != null && (
            <span data-testid="trade-bid-ask">
              Bid <span className="text-terminal-text">{liveUpdate.bid.toFixed(2)}</span>
              {' × '}
              Ask <span className="text-terminal-text">{liveUpdate.ask.toFixed(2)}</span>
            </span>
          )}
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

      {/* Concentration rail — non-blocking warning (M1.5) */}
      {concentrated && prospectiveWeight != null && (
        <p
          data-testid="trade-concentration-warning"
          className="mt-1.5 text-xs leading-tight text-terminal-amber"
        >
          ⚠ A buy this size would make {normalizedTicker} ~
          {Math.round(prospectiveWeight * 100)}% of your portfolio.
        </p>
      )}

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
