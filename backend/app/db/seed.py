"""Default seed data for FinAlly.

Inserts the default user profile and the 10-ticker watchlist.
All inserts use INSERT OR IGNORE for idempotency — safe to call on a
database that is already seeded.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from app.market.seed_prices import DEFAULT_WATCHLIST

logger = logging.getLogger(__name__)


def seed_db(
    conn: sqlite3.Connection,
    *,
    seed_cash: float = 10000.0,
    default_watchlist: list[str] | None = None,
) -> None:
    """Insert default user and watchlist tickers if they do not already exist.

    Uses ``INSERT OR IGNORE`` throughout so this function is safe to call
    multiple times without duplicating data.

    Args:
        conn: An open SQLite connection (caller retains ownership).
        seed_cash: Starting cash for the default user (CN-1: the active
            market profile's seed cash — $10,000 for us, ¥100,000 for cn).
        default_watchlist: Tickers to seed into the watchlist; None uses the
            US ``DEFAULT_WATCHLIST`` (the pre-CN-1 behavior).
    """
    now = datetime.now(timezone.utc).isoformat()

    # Default user profile (the anonymous user displays as 'Guest' — M4.1)
    conn.execute(
        "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at, display_name) "
        "VALUES (?, ?, ?, ?)",
        ("default", seed_cash, now, "Guest"),
    )

    # Default watchlist — the 10 default equities (crypto seeds exist in
    # SEED_PRICES but join only via an explicit watchlist add, M3.3), or the
    # active profile's universe watchlist when injected (CN-1).
    tickers = list(DEFAULT_WATCHLIST if default_watchlist is None else default_watchlist)
    for ticker in tickers:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), "default", ticker, now),
        )

    conn.commit()
    logger.info("Seeded default user and %d watchlist tickers", len(tickers))
