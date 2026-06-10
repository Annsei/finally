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
    let es: EventSource;
    let lastEventAt = Date.now();

    const connect = () => {
      es = new EventSource('/api/stream/prices');

      es.onopen = () => {
        lastEventAt = Date.now();
        setConnectionStatus('connected');
      };

      es.onmessage = (event) => {
        lastEventAt = Date.now();
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
  }, []); // Empty deps — one connection for the entire page lifetime.
}
