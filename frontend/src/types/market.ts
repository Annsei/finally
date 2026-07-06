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
  // Microstructure fields (Batch 2) — backend always sends them
  bid?: number;                // best bid (sells fill here)
  ask?: number;                // best ask (buys fill here)
  volume?: number;             // volume traded since the previous update
}

// GET /api/market/history response — 1-second OHLCV bars, ascending by time:
export interface HistoryBar {
  time: number;   // bucket start, Unix seconds (int)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarketHistoryResponse {
  ticker: string;
  bars: HistoryBar[];
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
  realized_pnl?: number;  // lifetime realized P&L across all sells
}

// Default tickers matching backend/app/market/seed_prices.py SEED_PRICES
export const DEFAULT_TICKERS = [
  'AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA',
  'NVDA', 'META', 'JPM', 'V', 'NFLX',
] as const;

// GET /api/market/events response (newest first) — sudden-move news feed:
export interface MarketEvent {
  id: string;
  ticker: string;
  headline: string;
  change_percent: number;  // signed single-tick move that triggered the event
  direction: 'up' | 'down';
  timestamp: number;       // Unix seconds (float)
}

export interface MarketEventsResponse {
  events: MarketEvent[];
}

// Orders (POST/GET /api/portfolio/orders, DELETE /api/portfolio/orders/{id}):
export type OrderStatus = 'open' | 'filled' | 'cancelled' | 'rejected' | 'expired';
export type OrderKind = 'limit' | 'stop' | 'stop_limit';
export type TimeInForce = 'day' | 'gtc';

export interface LimitOrder {
  id: string;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: number;
  kind: OrderKind;
  limit_price: number | null;   // null for pure stop orders
  stop_price: number | null;    // null for plain limit orders
  time_in_force: TimeInForce;
  expires_at: string | null;    // ISO; set for DAY orders
  triggered_at: string | null;  // stamped when a stop(-limit) trigger fires
  status: OrderStatus;
  reject_reason: string | null;
  created_at: string;      // ISO timestamp string
  filled_at: string | null;
  fill_price: number | null;
}

export interface OrdersResponse {
  orders: LimitOrder[];
}

export interface OrderPostResponse {
  order: LimitOrder;
}

// Standing rules (GET/POST /api/rules, PATCH/DELETE /api/rules/{id}) — M2.2:
export type RuleTriggerType =
  | 'price_above'
  | 'price_below'
  | 'day_change_pct_above'
  | 'day_change_pct_below';
export type RuleStatus = 'active' | 'paused' | 'fired';

export interface TradingRule {
  id: string;
  ticker: string;
  description: string;
  trigger_type: RuleTriggerType;
  threshold: number;
  side: 'buy' | 'sell';
  quantity: number;
  status: RuleStatus;
  created_at: string;
  last_fired_at: string | null;
  fire_count: number;
}

export interface RulesResponse {
  rules: TradingRule[];
}

// Chat action outcomes for AI-placed orders and AI-created rules (M2.1/2.2):
export interface ChatOrderOutcome {
  status: string; // open | filled | failed
  ticker: string;
  error?: string;
  side?: string;
  quantity?: number;
  kind?: OrderKind;
  limit_price?: number | null;
  stop_price?: number | null;
  fill_price?: number | null;
}

export interface ChatRuleOutcome {
  status: 'created' | 'failed';
  rule?: TradingRule;
  ticker?: string;
  error?: string;
}

// GET /api/portfolio/trades response (newest first):
export interface TradeRecord {
  id: string;
  ticker: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  executed_at: string;  // ISO timestamp string
  commission?: number;            // 0 unless FINALLY_COMMISSION_BPS is set
  realized_pnl?: number | null;   // sells only; null for buys
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
    orders?: ChatOrderOutcome[];
    rules?: ChatRuleOutcome[];
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
  orders?: ChatOrderOutcome[];
  rules?: ChatRuleOutcome[];
}
