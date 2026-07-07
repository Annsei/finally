-- FinAlly database schema
-- All tables use user_id defaulting to "default" for single-user use
-- while keeping the door open for future multi-user support.

-- User profile: cash balance and account state.
-- display_name (M4.1) is the login name with its original casing; the row id
-- is the lowercased name. The anonymous 'default' row displays as 'Guest'.
-- NOTE: new columns here must also be added to _migrate_schema() in
-- connection.py — CREATE TABLE IF NOT EXISTS does not evolve existing tables.
CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY,
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL,
    display_name TEXT
);

-- App-level key/value metadata (M4.1). Holds the HMAC session secret
-- ('session_secret'), generated once at first boot by init_db().
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Watchlist: tickers the user wants to track
CREATE TABLE IF NOT EXISTS watchlist (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL DEFAULT 'default',
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- Positions: current holdings (one row per ticker per user).
-- t1_locked (CN-2) is the share count bought today that the T+1 rule keeps
-- non-sellable until the next trading day; 0 in markets without T+1 (us).
-- NOTE: new columns here must also be added to _migrate_schema() in
-- connection.py — CREATE TABLE IF NOT EXISTS does not evolve existing tables.
CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
    t1_locked  REAL NOT NULL DEFAULT 0,
    UNIQUE (user_id, ticker)
);

-- Trades: append-only log of all executed orders.
-- commission is the fee charged on the fill (0 unless FINALLY_COMMISSION_BPS
-- is set). realized_pnl is set on sells only: (fill_price - avg_cost_at_sale)
-- * quantity - commission, rounded to 2dp; NULL for buys.
-- NOTE: new columns here must also be added to _migrate_schema() in
-- connection.py — CREATE TABLE IF NOT EXISTS does not evolve existing tables.
CREATE TABLE IF NOT EXISTS trades (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL,
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,
    commission   REAL NOT NULL DEFAULT 0,
    realized_pnl REAL,
    executed_at  TEXT NOT NULL
);

-- Portfolio snapshots: total value over time for P&L chart
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

-- Chat messages: conversation history with the LLM assistant.
-- kind marks who initiated the message and why (M2.3/M2.4):
--   'chat'   — ordinary conversation turns (user + assistant)
--   'brief'  — assistant-initiated event-driven AI brief (M2.3)
--   'review' — daily AI review generated via POST /api/chat/review (M2.4)
--   'rule'   — rule-fired activation record written by the rules evaluator
-- Only kind='chat' rows feed the LLM conversation history; GET /api/chat/
-- returns all kinds. NOTE: new columns here must also be added to
-- _migrate_schema() in connection.py — CREATE TABLE IF NOT EXISTS does not
-- evolve existing tables.
CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    actions    TEXT,
    kind       TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL
);

-- Orders: resting orders processed by the background fill loop.
-- kind is one of 'limit', 'stop', 'stop_limit'; status is one of 'open',
-- 'filled', 'cancelled', 'rejected', 'expired'. limit_price is NULL for
-- 'stop' orders; stop_price is NULL for 'limit' orders. time_in_force is
-- 'gtc' (expires_at NULL) or 'day' (expires_at = created_at + 24h until the
-- M3 session clock lands). triggered_at is stamped when a stop/stop_limit
-- order's trigger condition first fires; always NULL for 'limit'.
-- init_db() executes this script on every startup (even for pre-existing
-- database files), so old deployments pick this table up idempotently via
-- IF NOT EXISTS. NOTE: new columns here must also be added to
-- _migrate_schema() in connection.py — CREATE TABLE IF NOT EXISTS does not
-- evolve existing tables.
CREATE TABLE IF NOT EXISTS orders (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    ticker        TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      REAL NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'limit',
    limit_price   REAL,
    stop_price    REAL,
    time_in_force TEXT NOT NULL DEFAULT 'gtc',
    expires_at    TEXT,
    triggered_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    filled_at     TEXT,
    fill_price    REAL,
    fill_trade_id TEXT
);

-- Rules: standing one-shot automations evaluated every ~1s against live
-- quotes (M2.2). trigger_type is one of 'price_above', 'price_below',
-- 'day_change_pct_above', 'day_change_pct_below'; status is one of 'active',
-- 'paused', 'fired'. Rules are one-shot: on firing they move to 'fired' (even
-- when the resulting trade fails validation) and must be re-armed via
-- PATCH /api/rules/{id} {"status": "active"} to fire again. init_db() executes
-- this script on every startup, so pre-existing database volumes pick this
-- table up idempotently via IF NOT EXISTS (new table — no column migration).
CREATE TABLE IF NOT EXISTS rules (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    ticker        TEXT NOT NULL,
    description   TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,
    threshold     REAL NOT NULL,
    side          TEXT NOT NULL,
    quantity      REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    last_fired_at TEXT,
    fire_count    INTEGER NOT NULL DEFAULT 0
);

-- Seasons (M4.3): one row per competitive season. Exactly one season has
-- ended_at IS NULL (the current one); init_db() inserts season 1 when the
-- table is empty. POST /api/season/reset stamps ended_at and inserts the next.
CREATE TABLE IF NOT EXISTS seasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT
);

-- Season results (M4.3): final standings archived by POST /api/season/reset.
CREATE TABLE IF NOT EXISTS season_results (
    season_id   INTEGER NOT NULL,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    final_value REAL NOT NULL,
    return_pct  REAL NOT NULL,
    rank        INTEGER NOT NULL,
    PRIMARY KEY (season_id, user_id)
);

-- Indexes for the hot query paths (chat history and P&L chart both filter by
-- user_id and order by timestamp; the fill loop scans open orders and the
-- rules evaluator scans active rules every second). init_db() executes this
-- script on every
-- startup, so existing databases pick these up idempotently via IF NOT EXISTS.
CREATE INDEX IF NOT EXISTS idx_chat_messages_user_created
    ON chat_messages (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_recorded
    ON portfolio_snapshots (user_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_orders_user_status
    ON orders (user_id, status);
CREATE INDEX IF NOT EXISTS idx_rules_user_status
    ON rules (user_id, status);
