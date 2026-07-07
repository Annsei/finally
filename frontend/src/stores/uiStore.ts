/**
 * uiStore.ts — cross-component UI state (PLATFORM_ROADMAP.md M5).
 *
 * PortfolioTabs owns which tab renders; other components need to switch it
 * remotely — RulesTable's per-rule "test" button jumps to the Backtest tab
 * with the rule's config prefilled. A tiny Zustand store keeps that wiring
 * out of prop drilling through the page layout.
 */
import { create } from 'zustand';
import type { RuleTriggerType } from '@/types/market';

export type PortfolioTab =
  | 'positions'
  | 'orders'
  | 'fills'
  | 'rules'
  | 'backtest'
  | 'analytics'
  | 'board';

// The subset of a backtest config a standing rule can seed (buy-entry only).
export interface BacktestPrefill {
  ticker: string;
  trigger_type: RuleTriggerType;
  threshold: number;
  quantity: number;
}

interface UiState {
  portfolioTab: PortfolioTab;
  setPortfolioTab: (tab: PortfolioTab) => void;
  // One-shot handoff: BacktestPanel consumes it and clears it back to null.
  backtestPrefill: BacktestPrefill | null;
  setBacktestPrefill: (prefill: BacktestPrefill | null) => void;
}

export const useUiStore = create<UiState>((set) => ({
  portfolioTab: 'positions',
  setPortfolioTab: (tab) => set({ portfolioTab: tab }),
  backtestPrefill: null,
  setBacktestPrefill: (prefill) => set({ backtestPrefill: prefill }),
}));
