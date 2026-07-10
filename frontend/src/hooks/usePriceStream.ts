import { useEffect } from 'react';
import { usePriceStore } from '@/stores/priceStore';
import type { PriceMap, PriceUpdate } from '@/types/market';

const DIRECTIONS = new Set<PriceUpdate['direction']>(['up', 'down', 'flat']);
const ASSET_CLASSES = new Set<NonNullable<PriceUpdate['asset_class']>>(['equity', 'crypto']);
const TICKER_RE = /^[A-Z0-9.:-]{1,20}$/;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isOptionalFiniteNumber(value: unknown): value is number | undefined {
  return value === undefined || isFiniteNumber(value);
}

function isOptionalNullableFiniteNumber(value: unknown): value is number | null | undefined {
  return value == null || isFiniteNumber(value);
}

/** Runtime guard for the untrusted JSON boundary of the public SSE stream. */
export function isPriceUpdate(value: unknown, tickerKey: string): value is PriceUpdate {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return false;
  const update = value as Record<string, unknown>;
  if (update.ticker !== tickerKey || !TICKER_RE.test(tickerKey)) return false;
  if (!isFiniteNumber(update.price) || update.price <= 0) return false;
  if (!isFiniteNumber(update.previous_price) || update.previous_price <= 0) return false;
  if (!isFiniteNumber(update.timestamp) || update.timestamp <= 0) return false;
  if (!isFiniteNumber(update.change) || !isFiniteNumber(update.change_percent)) return false;
  if (!DIRECTIONS.has(update.direction as PriceUpdate['direction'])) return false;

  for (const field of [
    'prev_close',
    'day_change',
    'day_change_percent',
    'day_high',
    'day_low',
    'bid',
    'ask',
    'volume',
  ] as const) {
    if (!isOptionalFiniteNumber(update[field])) return false;
  }
  if (
    !isOptionalNullableFiniteNumber(update.limit_up) ||
    !isOptionalNullableFiniteNumber(update.limit_down)
  ) {
    return false;
  }
  if (
    update.asset_class != null &&
    !ASSET_CLASSES.has(update.asset_class as NonNullable<PriceUpdate['asset_class']>)
  ) {
    return false;
  }
  return true;
}

/** Parse and sanitize a complete price snapshot; null means reject the frame. */
export function parsePricePayload(raw: string): PriceMap | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return null;

  const prices: PriceMap = {};
  for (const [ticker, update] of entries) {
    if (!isPriceUpdate(update, ticker)) return null;
    prices[ticker] = update;
  }
  return prices;
}

/**
 * usePriceStream — opens a single EventSource connection to /api/stream/prices
 * and feeds incoming price data into the Zustand price store.
 *
 * Security:
 * - T-03-XSS/T-03-PP: JSON.parse only (no eval); parse failures silently dropped
 * - T-03-DoS: useEffect cleanup always calls es.close()
 * - T-03-SSR: EventSource only referenced inside useEffect (never at module scope)
 *
 * Call this hook exactly once at the page root (pages/index.tsx).
 */
export function usePriceStream() {
  // Separate selectors — avoids Zustand v5 object-selector "Maximum update depth" pitfall
  const setPrices = usePriceStore((s) => s.setPrices);
  const setConnectionStatus = usePriceStore((s) => s.setConnectionStatus);

  useEffect(() => {
    // EventSource MUST be inside useEffect — not at module or render scope.
    // Next.js pre-renders pages in Node.js where EventSource is undefined (Pitfall 3).
    let es: EventSource;
    let lastEventAt = Date.now();

    const connect = () => {
      es = new EventSource('/api/stream/prices');

      es.onopen = () => {
        lastEventAt = Date.now();
        setConnectionStatus('connected');
      };

      // A named heartbeat is emitted even when a market is legitimately quiet
      // (closed session / CN midday break). It proves transport liveness without
      // pretending that a new price tick occurred.
      es.addEventListener('heartbeat', () => {
        lastEventAt = Date.now();
      });

      es.onmessage = (event) => {
        lastEventAt = Date.now();
        const data = parsePricePayload(event.data);
        // Reject syntactically valid but structurally unsafe frames as well as
        // malformed JSON. The last valid snapshot remains visible.
        if (data) setPrices(data);
      };

      es.onerror = () => {
        // EventSource auto-reconnects; CONNECTING state occurs during reconnect interval.
        if (es.readyState === EventSource.CONNECTING) {
          setConnectionStatus('reconnecting');
        } else {
          setConnectionStatus('disconnected');
        }
      };
    };

    connect();

    // Staleness watchdog. The server pushes ~every 500ms, so a multi-second
    // silent gap means the connection is dead even if EventSource never fired
    // an error (e.g. network loss without a TCP reset leaves readyState OPEN
    // forever). Recreate the connection: on a dead network the new EventSource
    // errors immediately and keeps auto-retrying (status stays "reconnecting")
    // until the network returns and onopen flips it back to "connected".
    const STALE_MS = 5_000;
    const watchdog = setInterval(() => {
      if (Date.now() - lastEventAt <= STALE_MS) return;
      lastEventAt = Date.now(); // back off one full window before re-checking
      setConnectionStatus('reconnecting');
      // CONNECTING means EventSource is already retrying on its own — leave it.
      if (es.readyState !== EventSource.CONNECTING) {
        es.close();
        connect();
      }
    }, 2_000);

    return () => {
      // Always close on unmount — T-03-DoS mitigation, prevents resource leak.
      clearInterval(watchdog);
      es.close();
      setConnectionStatus('disconnected');
    };
  }, [setConnectionStatus, setPrices]);
}
