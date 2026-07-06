-- FinAlly database schema
-- All tables use user_id defaulting to "default" for single-user use
-- while keeping the door open for future multi-user support.

-- User profile: cash balance and account state
CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY,
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL
);

-- Watchlist: tickers the user wants to track
CREATE TABLE IF NOT EXISTS watchlist (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL DEFAULT 'default',
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- Positions: current holdings (one row per ticker per user)
CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
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

-- Chat messages: conversation history with the LLM assistant
CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    actions    TEXT,
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

-- Indexes for the hot query paths (chat history and P&L chart both filter by
-- user_id and order by timestamp; the fill loop scans open orders every
-- second). init_db() executes this script on every
-- startup, so existing databases pick these up idempotently via IF NOT EXISTS.
CREATE INDEX IF NOT EXISTS idx_chat_messages_user_created
    ON chat_messages (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_recorded
    ON portfolio_snapshots (user_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_orders_user_status
    ON orders (user_id, status);
