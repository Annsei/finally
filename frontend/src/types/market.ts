// All field names are snake_case — match backend PriceUpdate.to_dict() exactly
// Source: backend/app/market/models.py (to_dict method) and backend/app/routes/

export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;       // Unix seconds (float)
  change: number;
  change_percent: number;
  direction: 'up' | 'down' | 'flat';
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
