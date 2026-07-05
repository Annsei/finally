// All field names are snake_case — match backend PriceUpdate.to_dict() exactly
// Source: backend/app/market/models.py (to_dict method) and backend/app/routes/

export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;       // Unix seconds (float)
  change: number;          // tick-over-tick (drives flash animation)
  change_percent: number;
  direction: 'up' | 'down' | 'flat';
  // Day-session fields (Batch 1 realism) — optional so older payloads and
  // test fixtures without them stay valid; backend always sends them
  prev_close?: number;         // previous session close reference
  day_change?: number;         // price − prev_close
  day_change_percent?: number; // vs prev_close, what real platforms quote
  day_high?: number;
  day_low?: number;
}

// SSE event.data is a JSON object keyed by ticker symbol:
// { "AAPL": PriceUpdate, "GOOGL": PriceUpdate, ... }
export type PriceMap = Record<string, PriceUpdate>;

// GET /api/watchlist response:
export interface WatchlistEntry {
  ticker: string;
  added_at: string;              // ISO timestamp string
  price: number | null;          // null if not in price cache yet
  change_percent: number | null;
  direction: 'up' | 'down' | 'flat' | null;
  day_change_percent?: number | null;
}

export interface WatchlistResponse {
  tickers: WatchlistEntry[];
}

// GET /api/portfolio response:
export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  unrealized_pnl: number;
  pnl_pct: number;
}

export interface PortfolioResponse {
  cash: number;
  total_value: number;
  positions: Position[];
}

// Default tickers matching backend/app/market/seed_prices.py SEED_PRICES
export const DEFAULT_TICKERS = [
  'AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA',
  'NVDA', 'META', 'JPM', 'V', 'NFLX',
] as const;

// GET /api/portfolio/trades response (newest first):
export interface TradeRecord {
  id: string;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  executed_at: string;  // ISO timestamp string
}

export interface TradesResponse {
  trades: TradeRecord[];
}

// GET /api/portfolio/history response:
export interface PortfolioSnapshot {
  total_value: number;
  recorded_at: string;  // ISO timestamp string
}

export interface PortfolioHistoryResponse {
  snapshots: PortfolioSnapshot[];
}

// POST /api/portfolio/trade outcome (included in POST /api/chat response trades[]):
export interface TradeOutcome {
  status: 'executed' | 'failed';
  ticker: string;
  side?: string;
  quantity?: number;
  price?: number;
  trade_id?: string;
  error?: string;
}

// POST /api/chat watchlist_changes[] item:
// Success: {status: "added"|"removed", ticker, action}; failure: {status: "failed", ticker, error}
export interface WatchlistOutcome {
  status: string;
  ticker: string;
  action?: string;
  error?: string;
}

// GET /api/chat/ response message item:
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  actions: {
    trades: TradeOutcome[];
    watchlist_changes: WatchlistOutcome[];
  } | null;
  created_at: string;
}

// GET /api/chat/ response:
export interface ChatHistoryResponse {
  messages: ChatMessage[];
}

// POST /api/chat response:
export interface ChatPostResponse {
  message: string;
  trades: TradeOutcome[];
  watchlist_changes: WatchlistOutcome[];
}
