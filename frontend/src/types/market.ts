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
  asset_class?: 'equity' | 'crypto'; // crypto trades 24/7 (M3.3)
  // A-share price limits (FinAlly-CN) — null/absent on the US market, so the
  // 涨停/跌停 badges never appear there.
  limit_up?: number | null;    // 涨停 price (ceiling)
  limit_down?: number | null;  // 跌停 price (floor)
}

// GET /api/market/session (M3.1) — sim trading sessions:
export interface MarketSessionResponse {
  state: 'open' | 'closed';
  session_id: number;
  state_since: number;             // Unix seconds
  next_transition_at: number | null; // null in 24/7 mode
  now: number;
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
  narrative?: string | null; // LLM-generated news flavor (M3.2); null until enriched
  change_percent: number;  // signed single-tick move that triggered the event
  direction: 'up' | 'down';
  timestamp: number;       // Unix seconds (float)
}

export interface MarketEventsResponse {
  events: MarketEvent[];
}

// GET /api/market/events/archive (P1 §3.3) — durable event archive, newest
// first; `before` cursor pagination:
export interface MarketEventsArchiveResponse {
  events: MarketEvent[];
  has_more: boolean;
}

// GET /api/market/quotes (P1 §3.4) — full PriceCache snapshot, ascending by
// ticker; each quote is the SSE PriceUpdate payload plus the universe sector:
export interface MarketQuote extends PriceUpdate {
  sector: string;
}

export interface MarketQuotesResponse {
  quotes: MarketQuote[];
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

// Backtest data source (D1 §3) — 'synthetic' is the legacy GBM path (request
// field OMITTED for byte-identical behaviour); 'history' evaluates over real
// daily bars from the user-synced daily_bars store (days = trading days).
export type BacktestSource = 'synthetic' | 'history';

// D1 §3 — history-mode responses echo the evaluated daily-bar window.
export interface BacktestDateRange {
  from: string; // ISO date (YYYY-MM-DD)
  to: string;
}

// POST /api/backtest (M5) — stateless strategy backtest over synthetic GBM
// history. Buy-entry only; exits are modeled with take_profit_pct/stop_loss_pct.
export interface BacktestRequest {
  ticker: string;
  trigger_type: RuleTriggerType;
  threshold: number;
  side?: 'buy';
  quantity: number;
  take_profit_pct?: number | null;
  stop_loss_pct?: number | null;
  days?: number; // 5-120, default 30 (history: trading days, 20-750)
  runs?: number; // 1-50, default 1 (Monte Carlo re-runs; history requires 1)
  seed?: number; // omitted → backend draws one; echoed in config
  source?: BacktestSource; // omitted → synthetic (legacy path, D1 §3)
}

export interface BacktestConfig {
  ticker: string;
  trigger_type: RuleTriggerType;
  threshold: number;
  side: 'buy';
  quantity: number;
  take_profit_pct: number | null;
  stop_loss_pct: number | null;
  days: number;
  runs: number;
  seed: number | null; // history runs echo null (fully deterministic, D1 §3)
  commission_bps: number;
  anchor_price: number;
  // D1 §3 additions — absent on pre-D1 payloads (treat as synthetic):
  source?: string;
  date_range?: BacktestDateRange | null;
}

export interface BacktestStats {
  total_return_pct: number;
  buy_hold_return_pct: number;
  max_drawdown_pct: number;
  final_equity: number;
  fires: number;
  round_trips: number;
  win_rate: number | null; // null when no round trips
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null; // null when no gross losses
  commission_paid: number;
  rejections: { insufficient_cash: number };
}

export interface BacktestPoint {
  time: number; // Unix seconds (int), strictly ascending
  value: number;
}

export type BacktestTradeReason = 'trigger' | 'take_profit' | 'stop_loss' | 'horizon_end';

export interface BacktestTrade {
  time: number;
  side: 'buy' | 'sell';
  price: number;
  quantity: number;
  reason: BacktestTradeReason;
  pnl: number | null; // sells carry the round trip's realized P&L
}

export interface BacktestRunsSummary {
  runs: number;
  median_return_pct: number;
  p05_return_pct: number;
  p95_return_pct: number;
  positive_share: number; // fraction of runs with return > 0
  median_max_drawdown_pct: number;
}

export interface BacktestResponse {
  config: BacktestConfig;
  stats: BacktestStats;
  equity_curve: BacktestPoint[];
  baseline_curve: BacktestPoint[]; // frictionless buy & hold reference
  trades: BacktestTrade[];
  runs_summary: BacktestRunsSummary | null; // populated when runs > 1
}

// Chat backtest outcomes (M5) — compact stats only, never curves/trades:
export interface ChatBacktestOutcome {
  status: 'completed' | 'failed';
  ticker: string;
  error?: string;
  config?: BacktestConfig;
  stats?: BacktestStats;
}

// ---------------------------------------------------------------------------
// Strategies (P2 §6) — declarative condition groups, exits, sizing.
// ---------------------------------------------------------------------------
export type StrategyStatus = 'draft' | 'live' | 'paused' | 'archived';

// One declarative entry condition — validated against the backend registry
// (app/indicators.py FIELD_SPECS). `field` stays a string so new backend
// fields never break the frontend types.
export interface StrategyCondition {
  field: string; // price | day_change_pct | ma | ma_cross | ema_cross | rsi | window_high | window_low | pullback_from_high_pct
  op: 'above' | 'below';
  value?: number;
  params?: Record<string, number>; // period / fast / slow / minutes
}

// Exactly one of `all` / `any`, holding 1..5 conditions.
export type StrategyConditionGroup =
  | { all: StrategyCondition[] }
  | { any: StrategyCondition[] };

// All exits optional; deploy (draft → live) requires at least one non-empty.
export interface StrategyExits {
  take_profit_pct?: number | null;
  stop_loss_pct?: number | null;
  trailing_stop_pct?: number | null;
  max_holding_days?: number | null;
}

export type StrategySizing =
  | { mode: 'fixed_qty'; qty: number }
  | { mode: 'cash_pct'; pct: number };

// GET /api/strategies — config + status + counters + derived performance:
export interface Strategy {
  id: string;
  name: string;
  ticker: string;
  status: StrategyStatus;
  entry: StrategyConditionGroup;
  exits: StrategyExits;
  sizing: StrategySizing;
  template: string | null;
  created_at: string;           // ISO timestamp string
  deployed_at: string | null;
  open_qty: number;
  open_price: number | null;
  opened_at: string | null;
  entered_count: number;
  exited_count: number;
  last_fired_at: string | null;
  runs_count: number;           // saved backtest runs for this strategy
  realized_pnl: number;         // Σ realized P&L of this strategy's sells
}

export interface StrategiesResponse {
  strategies: Strategy[];
}

export interface StrategyResponse {
  strategy: Strategy;
}

// GET /api/strategies/{id}/performance:
export interface StrategyPerformanceStats {
  realized_pnl: number;
  round_trips: number;
  win_rate: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number;
  fires: number;
}

export interface StrategyPerformanceResponse {
  stats: StrategyPerformanceStats;
  equity_curve: BacktestPoint[]; // cumulative realized P&L (0-baseline)
  trades: TradeRecord[];         // this strategy's fills
}

// GET /api/strategies/templates — static registry; names/descriptions are
// rendered by the frontend via i18n `strategy.template.{key}.name/.desc`:
export interface StrategyTemplate {
  key: string; // dip_buyer | momentum_breakout | ma_golden_cross | grid_lite | rsi_rebound | trend_rider
  ticker_hint: string | null;
  entry: StrategyConditionGroup;
  exits: StrategyExits;
  sizing: StrategySizing;
}

export interface StrategyTemplatesResponse {
  templates: StrategyTemplate[];
}

// ---------------------------------------------------------------------------
// Run Library (P2 §5) — persisted backtest runs.
// ---------------------------------------------------------------------------
// Full run (POST /api/backtest/runs 201, GET /api/backtest/runs/{id}).
// `config` may be the legacy shape (BacktestConfig) or the extended strategy
// shape ({ticker, entry, exits, sizing, …, source: "strategy"}).
export interface BacktestRun {
  id: string;
  strategy_id: string | null;
  label: string | null;
  created_at: string; // ISO timestamp string
  config: (Partial<BacktestConfig> & Record<string, unknown>) & { ticker: string };
  stats: BacktestStats;
  equity_curve: BacktestPoint[];
  baseline_curve: BacktestPoint[];
  trades: BacktestTrade[];
  runs_summary: BacktestRunsSummary | null;
}

export interface BacktestRunResponse {
  run: BacktestRun;
}

// GET /api/backtest/runs list item — stats only, no curves:
export interface BacktestRunListItem {
  id: string;
  strategy_id: string | null;
  label: string | null;
  created_at: string;
  ticker: string;
  days: number;
  runs: number;
  // D1 §3 — history runs are deterministic; their stored config echoes seed
  // as null (synthetic runs keep a numeric seed).
  seed: number | null;
  stats: BacktestStats;
  // D1 §5 — data-source marker passed through from the stored config; absent
  // on pre-D1 rows (rendered as synthetic).
  source?: string | null;
  date_range?: BacktestDateRange | null;
}

export interface BacktestRunsListResponse {
  runs: BacktestRunListItem[];
}

// Chat strategy outcomes (P2 §7) — create/backtest/deploy/pause actions:
export interface StrategyOutcome {
  status: 'created' | 'deployed' | 'paused' | 'completed' | 'failed';
  action?: 'create' | 'backtest' | 'deploy' | 'pause';
  strategy_id?: string;
  name?: string;
  ticker?: string;
  error?: string;
  // backtest action only — compact stats plus the persisted Run Library id:
  run_id?: string;
  stats?: BacktestStats;
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

// ---------------------------------------------------------------------------
// Developer API keys (P3 §5/§6) — the plaintext key appears ONLY in the
// create response; every other payload carries metadata + display prefix.
// ---------------------------------------------------------------------------
export interface ApiKeyInfo {
  id: string;
  label: string;
  prefix: string; // first 11 chars of the plaintext ("fk_XXXXXXXX") for display
  created_at: string; // ISO timestamp string
  last_used_at: string | null;
  // SQLite stores 0/1 — accept either encoding and normalize with Boolean().
  frozen: boolean | number;
  allowed_tickers: string[] | null; // null = unrestricted
  max_order_qty: number | null; // null = unrestricted
  daily_trade_cap: number | null; // null = unrestricted
}

// GET /api/keys:
export interface ApiKeysResponse {
  keys: ApiKeyInfo[];
}

// POST /api/keys 201 — `key` is the one-time plaintext (never shown again):
export interface ApiKeyCreateResponse {
  key: string;
  info: ApiKeyInfo;
}

// GET /api/keys/{id}/audit?limit=&before= entries:
export type ApiAuditResult = 'ok' | 'denied' | 'error' | 'rate_limited';

// key_id is NOT part of an entry — the endpoint is already key-scoped and the
// backend row serializer never returns it.
export interface ApiAuditEntry {
  id: string;
  method: string;
  endpoint: string;
  payload_digest: string | null; // SHA-256 hex digest of the request body (64 chars; raw payload not stored, for privacy)
  result: ApiAuditResult;
  status_code: number | null;
  created_at: string; // ISO timestamp string (also the `before` cursor)
}

export interface ApiAuditResponse {
  entries: ApiAuditEntry[];
  has_more: boolean;
}

// GET /api/market/sentiment (P4 §1) — cache-wide market temperature. All axes
// and the composite score are 0..100; label is one of the five band keys the
// frontend renders via i18n (market.sentimentLabel.*).
export interface MarketSentimentResponse {
  score: number;
  label: string; // frozen | cool | neutral | active | hot
  axes: { breadth: number; volatility: number; volume: number };
  sample_size: number;
}

// GET /api/market/correlation?minutes= (P4 §2) — Pearson correlation of 1m log
// returns, tickers pre-sorted by sector; matrix[i][j] pairs tickers[i]/[j].
export interface MarketCorrelationResponse {
  tickers: string[];
  sectors: Record<string, string>;
  matrix: number[][];
  minutes: number;
}

// GET /api/players/{user_id} (P4 §4) — public player profile. SUMMARY ONLY:
// equity curve + position weight %, never quantities/costs/cash. When the
// profile is private (and the viewer isn't the owner) only {user, public:false}
// comes back, so every detail field is optional.
export interface PlayerEquityPoint {
  time: number | string; // Unix seconds or ISO timestamp — both accepted
  value: number;
}

export interface PlayerPositionWeight {
  ticker: string;
  weight_pct: number; // 1dp share of total portfolio value
}

export interface PlayerProfileResponse {
  user: { id: string; name: string; created_at?: string };
  /**
   * The ACTUAL stored privacy flag. An owner viewing their own private
   * profile still gets the full payload — with `public: false` — so the
   * presence of detail fields, not this flag, decides the private empty state.
   */
  public: boolean;
  /** Duplicate of `public` on the full shape; the privacy toggle prefers it. */
  profile_public?: boolean;
  total_value?: number;
  return_pct?: number;
  rank?: number | null;
  equity_curve?: PlayerEquityPoint[];
  positions_summary?: PlayerPositionWeight[];
}

// PATCH /api/players/me (P4 §4) — cookie identity only:
export interface PlayerPrivacyResponse {
  public: boolean;
}

// Auth (M4.1) — anonymous requests act as the 'default' Guest user:
export interface AuthUser {
  id: string;
  name: string;
}

export interface AuthMeResponse {
  user: AuthUser;
}

// GET /api/leaderboard (M4.2):
export interface LeaderboardEntry {
  user_id: string;
  name: string;
  total_value: number;
  return_pct: number;
  rank: number;
}

export interface LeaderboardResponse {
  season: { id: number; started_at: string };
  entries: LeaderboardEntry[];
}

// GET /api/seasons (M4.3) — every season, newest first; archived results only
// for ended seasons (the current season's `results` is null):
export interface SeasonResultEntry {
  user_id: string;
  name: string;
  final_value: number;
  return_pct: number;
  rank: number;
}

export interface Season {
  id: number;
  started_at: string;          // ISO timestamp string
  ended_at: string | null;     // null while the season is in progress
  results: SeasonResultEntry[] | null;
}

export interface SeasonsResponse {
  seasons: Season[];
}

// ---------------------------------------------------------------------------
// Timed private competitions (D2 §3) — status is derived server-side from
// starts_at/ends_at at read time; no background loop.
// ---------------------------------------------------------------------------
export type CompetitionStatus = 'upcoming' | 'running' | 'ended';

// GET /api/competitions?scope=mine|all list item. `code` is present only on
// scope=mine rows the caller created (share-to-join stays creator-controlled).
export interface CompetitionSummary {
  id: string;
  name: string;
  code?: string | null;
  status: CompetitionStatus;
  member_count: number;
  starts_at: string; // ISO timestamp string
  ends_at: string;   // ISO timestamp string
}

export interface CompetitionsListResponse {
  competitions: CompetitionSummary[];
}

// POST /api/competitions 201 — the created competition (creator auto-joined):
export interface CompetitionCreateResponse {
  competition: CompetitionSummary;
}

// GET /api/competitions/{id} board row — running: live standings; ended: the
// member's last portfolio_snapshot before ends_at (baseline fallback → 0%).
export interface CompetitionBoardRow {
  user_id: string;
  name: string;
  baseline_value: number;
  value: number;
  return_pct: number;
  rank: number;
}

export interface CompetitionDetailResponse extends CompetitionSummary {
  board: CompetitionBoardRow[];
}

// GET /api/portfolio/analytics (M3.4):
export interface AnalyticsTradeRef {
  ticker: string;
  side: string;
  quantity: number;
  price: number;
  realized_pnl: number;
  executed_at: string;
}

export interface SectorAllocation {
  sector: string;
  value: number;
  weight: number; // fraction of total portfolio value
}

export interface AnalyticsResponse {
  total_trades: number;
  sell_trades: number;
  win_rate: number | null;
  realized_pnl: number;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  best_trade: AnalyticsTradeRef | null;
  worst_trade: AnalyticsTradeRef | null;
  sector_allocation: SectorAllocation[];
  // D2 §4 additive risk keys — optional so pre-D2 payloads stay valid.
  // var_95_pct: 1-day historical VaR as a POSITIVE loss % (2dp); beta vs the
  // equal-weight universe benchmark. Both null when <20 common bars / no
  // positions / zero benchmark variance; risk_window_bars = bars used (0 = none).
  var_95_pct?: number | null;
  beta?: number | null;
  risk_window_bars?: number;
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
export type ChatMessageKind = 'chat' | 'brief' | 'review' | 'rule' | 'strategy';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  kind?: ChatMessageKind; // agent-initiated messages: market briefs, reviews, rule/strategy firings
  actions: {
    trades: TradeOutcome[];
    watchlist_changes: WatchlistOutcome[];
    orders?: ChatOrderOutcome[];
    rules?: ChatRuleOutcome[];
    backtests?: ChatBacktestOutcome[];
    strategies?: StrategyOutcome[];
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
  backtests?: ChatBacktestOutcome[];
  strategies?: StrategyOutcome[];
}

// ---------------------------------------------------------------------------
// Historical daily-bar data layer (D1 §2) — coverage + user-triggered sync.
// ---------------------------------------------------------------------------
// GET /api/market/history/coverage — one row per ticker with stored bars:
export interface HistoryCoverageRow {
  ticker: string;
  from: string; // ISO date of the earliest stored bar
  to: string; // ISO date of the latest stored bar
  count: number;
  source: string; // sample | yfinance | akshare
}

// POST /api/market/history/sync {source?, tickers?, years?} — per-ticker
// outcome. A row succeeded iff `bars` > 0; auto-mode fallback rows persist
// bars via sample yet still carry the real source's `error` as an annotation.
export interface HistorySyncResult {
  ticker: string;
  source?: string;
  bars?: number;
  error?: string;
}

export interface HistorySyncResponse {
  results: HistorySyncResult[];
  total_bars?: number;
}
