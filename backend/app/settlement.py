"""Session settlement hooks for FinAlly (M3.1).

Wired into the session clock loop by main.py's lifespan:

- ``settle_session_close(price_cache, db_path)`` runs once at each market
  CLOSE. It (1) stamps every tracked EQUITY ticker's current (frozen) price
  as the official session close in the PriceCache — that close becomes the
  ticker's ``prev_close`` when the next session opens — and (2) expires all
  OPEN orders on EQUITY tickers with time_in_force='day' (status 'expired'),
  superseding their 24h TTL. Crypto is exempt on both counts: crypto tickers
  trade 24/7 and crypto DAY orders keep the 24h ``expires_at`` behavior
  enforced by the fill loop.

- ``roll_session_open(price_cache)`` runs once at each market OPEN. It rolls
  equity day-session state in the cache (``PriceCache.roll_session``):
  ``prev_close`` becomes the settled close, day extremes reset, day change
  restarts from zero.

Both hooks are synchronous and open/close their own DB connection where
needed, mirroring the snapshot loop's style. In 24/7 mode (default for tests,
real Massive data, or unset/invalid env config) the session clock never
transitions and these hooks never run.
"""

from __future__ import annotations

import logging

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.seed_prices import asset_class_for

logger = logging.getLogger(__name__)


def _tracked_equity_tickers(price_cache: PriceCache) -> list[str]:
    """All tickers currently in the cache whose asset class is 'equity'."""
    return [
        ticker
        for ticker in price_cache.get_all()
        if asset_class_for(ticker) == "equity"
    ]


def settle_session_close(price_cache: PriceCache, db_path: str) -> dict:
    """Settle the closing session. Runs once per open→closed transition.

    Args:
        db_path: SQLite database path — a dedicated connection is opened
            (and always closed) for the DAY-order expiry.

    Returns:
        {"closes": {ticker: close}, "expired_orders": n} for logging/tests.
    """
    equity_tickers = _tracked_equity_tickers(price_cache)
    closes = price_cache.settle_close(equity_tickers)

    expired = 0
    conn = get_conn(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, ticker FROM orders "
            "WHERE user_id = 'default' AND status = 'open' AND time_in_force = 'day'"
        ).fetchall()
        for row in rows:
            if asset_class_for(row["ticker"]) != "equity":
                continue  # Crypto DAY orders keep their 24h TTL (fill loop).
            cur = conn.execute(
                "UPDATE orders SET status = 'expired' WHERE id = ? AND status = 'open'",
                (row["id"],),
            )
            expired += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "Session close settled: %d equity closes stamped, %d DAY orders expired",
        len(closes),
        expired,
    )
    return {"closes": closes, "expired_orders": expired}


def roll_session_open(price_cache: PriceCache) -> None:
    """Roll equity day-session state at reopen. Runs once per closed→open transition."""
    equity_tickers = _tracked_equity_tickers(price_cache)
    price_cache.roll_session(equity_tickers)
    logger.info("Session open: rolled day state for %d equity tickers", len(equity_tickers))
