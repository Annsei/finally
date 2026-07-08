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
  // P1 §2 — chat state lives here so the panel survives page navigation:
  // open/closed state and the input draft persist across / ↔ /market ↔ … .
  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
  chatDraft: string;
  setChatDraft: (draft: string) => void;
  // One-shot: a page (e.g. /symbol "AI analyze") sets it; ChatPanel's effect
  // consumes it — sends it as a user message and clears it back to null.
  pendingChatMessage: string | null;
  setPendingChatMessage: (message: string | null) => void;
}

export const useUiStore = create<UiState>((set) => ({
  portfolioTab: 'positions',
  setPortfolioTab: (tab) => set({ portfolioTab: tab }),
  backtestPrefill: null,
  setBacktestPrefill: (prefill) => set({ backtestPrefill: prefill }),
  chatOpen: true,
  setChatOpen: (open) => set({ chatOpen: open }),
  chatDraft: '',
  setChatDraft: (draft) => set({ chatDraft: draft }),
  pendingChatMessage: null,
  setPendingChatMessage: (message) => set({ pendingChatMessage: message }),
}));
