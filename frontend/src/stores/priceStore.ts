import { create } from 'zustand';
import type { PriceMap, PriceUpdate } from '@/types/market';

interface PriceStore {
  prices: PriceMap;
  connectionStatus: 'connected' | 'reconnecting' | 'disconnected';
  setPrices: (data: PriceMap) => void;
  setConnectionStatus: (status: PriceStore['connectionStatus']) => void;
}

/** Shallow value equality is sufficient: PriceUpdate contains scalars only. */
export function samePriceUpdate(a: PriceUpdate | undefined, b: PriceUpdate): boolean {
  if (a === b) return true;
  if (!a) return false;
  const aRecord = a as unknown as Record<string, unknown>;
  const bRecord = b as unknown as Record<string, unknown>;
  const aKeys = Object.keys(aRecord);
  const bKeys = Object.keys(bRecord);
  if (aKeys.length !== bKeys.length) return false;
  return bKeys.every((key) => Object.is(aRecord[key], bRecord[key]));
}

/**
 * Reconcile a complete SSE snapshot while preserving each unchanged ticker's
 * object identity. Zustand selectors such as useTicker('AAPL') then skip a
 * render when only another symbol changed. A byte-for-byte identical frame
 * also keeps the map identity, so no price subscriber is notified at all.
 */
export function mergePriceMaps(previous: PriceMap, incoming: PriceMap): PriceMap {
  const previousKeys = Object.keys(previous);
  const incomingKeys = Object.keys(incoming);
  let changed = previousKeys.length !== incomingKeys.length;
  const merged: PriceMap = {};

  for (const ticker of incomingKeys) {
    const nextUpdate = incoming[ticker];
    const currentUpdate = previous[ticker];
    if (samePriceUpdate(currentUpdate, nextUpdate)) {
      merged[ticker] = currentUpdate;
    } else {
      merged[ticker] = nextUpdate;
      changed = true;
    }
  }

  return changed ? merged : previous;
}

export const usePriceStore = create<PriceStore>()((set) => ({
  prices: {},
  connectionStatus: 'disconnected',
  setPrices: (data) =>
    set((state) => {
      const prices = mergePriceMaps(state.prices, data);
      return prices === state.prices ? state : { prices };
    }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}));

// Per-ticker selector — only re-renders when THIS ticker's data changes.
// Returns a single atom (PriceUpdate | undefined), never an object literal.
// This avoids the Zustand v5 "Maximum update depth exceeded" pitfall.
export const useTicker = (ticker: string) =>
  usePriceStore((state) => state.prices[ticker]);
