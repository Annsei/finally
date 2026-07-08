"""SQLite connection utilities for FinAlly.

Provides per-request connection management with WAL mode and dict-like row access.
Database initialization is idempotent — safe to call on every startup.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH: str = os.getenv("DB_PATH", "db/finally.db")

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection configured for FinAlly use.

    Settings applied:
    - ``row_factory = sqlite3.Row`` for dict-like row access
    - ``PRAGMA journal_mode=WAL`` for concurrent read throughput
    - ``PRAGMA foreign_keys=ON`` for referential integrity

    The caller is responsible for closing the connection.

    Args:
        db_path: Path to the SQLite database file. Defaults to DB_PATH.

    Returns:
        An open :class:`sqlite3.Connection`.
    """
    # Ensure the parent directory exists (important for the default db/ path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _needs_seed(conn: sqlite3.Connection) -> bool:
    """Return True if users_profile is empty (database has not been seeded yet)."""
    row = conn.execute("SELECT COUNT(*) FROM users_profile").fetchone()
    return row[0] == 0


# Columns added after the tables first shipped. CREATE TABLE IF NOT EXISTS in
# schema.sql does NOT evolve existing tables, so pre-existing database volumes
# gain these via idempotent ALTER TABLE ADD COLUMN on startup (M1 migration).
_ORDERS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "TEXT NOT NULL DEFAULT 'limit'"),
    ("stop_price", "REAL"),
    ("time_in_force", "TEXT NOT NULL DEFAULT 'gtc'"),
    ("expires_at", "TEXT"),
    ("triggered_at", "TEXT"),
)
_TRADES_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("commission", "REAL NOT NULL DEFAULT 0"),
    ("realized_pnl", "REAL"),
    # P2: strategy-engine attribution — NULL for every non-strategy fill,
    # exactly the pre-P2 semantics for existing rows.
    ("strategy_id", "TEXT"),
)
# M2.3/M2.4: message kind ('chat' | 'brief' | 'review' | 'rule'). Pre-existing
# rows are ordinary conversation turns, so the default 'chat' is exactly right.
_CHAT_MESSAGES_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "TEXT NOT NULL DEFAULT 'chat'"),
)
# M4.1: login display name (original casing; row id is the lowercased name).
# Pre-existing rows get NULL; the anonymous 'default' row is stamped 'Guest'
# by _migrate_schema so /api/auth/me and the leaderboard always have a name.
_USERS_PROFILE_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("display_name", "TEXT"),
)
# CN-2: T+1 lock — shares bought today, non-sellable until the next session.
# Pre-existing rows get 0 (no lock), exactly the pre-CN-2 semantics.
_POSITIONS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("t1_locked", "REAL NOT NULL DEFAULT 0"),
)

# Rebuild target for the orders table: identical to schema.sql. Used only when
# an old database still has limit_price declared NOT NULL (stop orders store
# NULL there) — SQLite cannot drop NOT NULL via ALTER, so rebuild once.
_ORDERS_REBUILD_DDL = """
CREATE TABLE orders_m1_rebuild (
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
)
"""

_ORDERS_COLUMN_LIST = (
    "id, user_id, ticker, side, quantity, kind, limit_price, stop_price, "
    "time_in_force, expires_at, triggered_at, status, reject_reason, "
    "created_at, filled_at, fill_price, fill_trade_id"
)


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    """Return {column_name: pragma row} for ``table`` (empty if table absent)."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"]: row for row in rows}


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> list[str]:
    """ALTER TABLE ADD COLUMN for each column not already present. Idempotent."""
    existing = _table_columns(conn, table)
    added: list[str] = []
    for name, ddl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(f"{table}.{name}")
    return added


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Upgrade pre-existing databases to the current schema. Idempotent.

    Runs on every startup after schema.sql is applied. Two steps:

    1. Add columns that shipped after the tables were first created
       (PRAGMA table_info check -> ALTER TABLE ADD COLUMN). Existing rows get
       the column defaults (orders: kind='limit', time_in_force='gtc';
       trades: commission=0, realized_pnl NULL; chat_messages: kind='chat') —
       exactly the pre-migration semantics.
    2. Relax orders.limit_price to nullable (stop orders carry no limit price).
       SQLite cannot drop NOT NULL in place, so a one-time table rebuild
       (create new -> copy -> drop -> rename) runs when the old constraint is
       detected. The status index is recreated afterwards (DROP TABLE drops it).
    """
    added = _add_missing_columns(conn, "orders", _ORDERS_NEW_COLUMNS)
    added += _add_missing_columns(conn, "trades", _TRADES_NEW_COLUMNS)
    added += _add_missing_columns(conn, "chat_messages", _CHAT_MESSAGES_NEW_COLUMNS)
    added += _add_missing_columns(conn, "users_profile", _USERS_PROFILE_NEW_COLUMNS)
    added += _add_missing_columns(conn, "positions", _POSITIONS_NEW_COLUMNS)

    # M4.1: the anonymous 'default' user displays as 'Guest'. Idempotent —
    # only fills a missing name (pre-M4 rows migrated above get NULL).
    named = conn.execute(
        "UPDATE users_profile SET display_name = 'Guest' "
        "WHERE id = 'default' AND display_name IS NULL"
    ).rowcount

    limit_price_col = _table_columns(conn, "orders").get("limit_price")
    rebuilt = limit_price_col is not None and limit_price_col["notnull"]
    if rebuilt:
        conn.execute(_ORDERS_REBUILD_DDL)
        conn.execute(
            f"INSERT INTO orders_m1_rebuild ({_ORDERS_COLUMN_LIST}) "
            f"SELECT {_ORDERS_COLUMN_LIST} FROM orders"
        )
        conn.execute("DROP TABLE orders")
        conn.execute("ALTER TABLE orders_m1_rebuild RENAME TO orders")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_user_status ON orders (user_id, status)"
        )

    if added or rebuilt or named:
        conn.commit()
        logger.info(
            "Schema migration applied: added columns %s%s",
            added or "(none)",
            "; rebuilt orders table for nullable limit_price" if rebuilt else "",
        )


def _ensure_arena_state(conn: sqlite3.Connection) -> None:
    """Ensure M4 singleton state exists. Idempotent; runs on every startup.

    - ``app_meta['session_secret']``: HMAC key for the ``finally_session``
      cookie, generated once at first boot (stdlib ``secrets``).
    - ``seasons``: season 1 is inserted when the table is empty so the
      leaderboard always has a current (ended_at IS NULL) season.
    """
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'session_secret'"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('session_secret', ?)",
            (secrets.token_hex(32),),
        )

    season_count = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
    if season_count == 0:
        conn.execute(
            "INSERT INTO seasons (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )

    conn.commit()


def init_db(
    db_path: str = DB_PATH,
    *,
    seed_cash: float = 10_000.0,
    default_watchlist: list[str] | None = None,
) -> None:
    """Initialize the database: create schema and seed default data if empty.

    Idempotent — uses ``CREATE TABLE IF NOT EXISTS`` so calling this multiple
    times does not raise errors or duplicate data, then runs the column
    migration step (``_migrate_schema``) so pre-existing database volumes
    pick up columns added after their tables were first created.

    Args:
        db_path: Path to the SQLite database file. Defaults to DB_PATH.
        seed_cash: Starting cash for the default user when seeding a fresh
            database (CN-1: the active market profile's seed cash). An
            already-seeded database is never re-seeded — existing balances
            and watchlists are untouched regardless of these values.
        default_watchlist: Tickers to seed into a fresh watchlist; None uses
            the US ``DEFAULT_WATCHLIST`` (the pre-CN-1 behavior).
    """
    logger.info("Initializing database at %s", db_path)
    conn = get_conn(db_path)
    try:
        schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        _migrate_schema(conn)
        _ensure_arena_state(conn)
        logger.debug("Schema applied successfully")

        if _needs_seed(conn):
            logger.info("Database is empty — running seed")
            from app.db.seed import seed_db  # local import to avoid circular at module level

            seed_db(conn, seed_cash=seed_cash, default_watchlist=default_watchlist)
        else:
            logger.debug("Database already seeded — skipping")
    finally:
        conn.close()
