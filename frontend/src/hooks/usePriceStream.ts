import { useEffect } from 'react';
import { usePriceStore } from '@/stores/priceStore';

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
    const es = new EventSource('/api/stream/prices');

    es.onopen = () => {
      setConnectionStatus('connected');
    };

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // data is PriceMap: { AAPL: PriceUpdate, GOOGL: PriceUpdate, ... }
        setPrices(data);
      } catch {
        // Silently ignore malformed events — T-03-PP mitigation.
        // Do NOT rethrow; a bad SSE frame must never crash the UI.
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; CONNECTING state occurs during reconnect interval.
      if (es.readyState === EventSource.CONNECTING) {
        setConnectionStatus('reconnecting');
      } else {
        setConnectionStatus('disconnected');
      }
    };

    return () => {
      // Always close on unmount — T-03-DoS mitigation, prevents resource leak.
      es.close();
      setConnectionStatus('disconnected');
    };
  }, []); // Empty deps — one connection for the entire page lifetime.
}
