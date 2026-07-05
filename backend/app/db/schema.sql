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

-- Trades: append-only log of all executed orders
CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
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

-- Limit orders: resting orders filled by the background fill loop when the
-- market crosses the limit price. status is one of 'open', 'filled',
-- 'cancelled', 'rejected'. init_db() executes this script on every startup
-- (even for pre-existing database files), so old deployments pick this table
-- up idempotently via IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS orders (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    ticker        TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      REAL NOT NULL,
    limit_price   REAL NOT NULL,
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
