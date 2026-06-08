import { create } from 'zustand';
import type { PriceUpdate, PriceMap } from '@/types/market';

interface PriceStore {
  prices: PriceMap;
  connectionStatus: 'connected' | 'reconnecting' | 'disconnected';
  setPrices: (data: PriceMap) => void;
  setConnectionStatus: (status: PriceStore['connectionStatus']) => void;
}

export const usePriceStore = create<PriceStore>()((set) => ({
  prices: {},
  connectionStatus: 'disconnected',
  setPrices: (data) => set({ prices: data }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}));

// Per-ticker selector — only re-renders when THIS ticker's data changes.
// Returns a single atom (PriceUpdate | undefined), never an object literal.
// This avoids the Zustand v5 "Maximum update depth exceeded" pitfall.
export const useTicker = (ticker: string) =>
  usePriceStore((state) => state.prices[ticker]);
