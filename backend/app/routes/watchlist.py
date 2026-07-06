"""Watchlist API routes for FinAlly.

Provides:
- GET /api/watchlist — current watchlist tickers with live prices from cache
- POST /api/watchlist — add a ticker to the watchlist (idempotent)
- DELETE /api/watchlist/{ticker} — remove a ticker from the watchlist (idempotent)

All routes are created via the factory function ``create_watchlist_router`` which
closes over the shared ``PriceCache`` instance and the database path.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.connection import get_conn
from app.market.cache import PriceCache

logger = logging.getLogger(__name__)


class AddTickerRequest(BaseModel):
    ticker: str


def ticker_watched_by_anyone(conn: sqlite3.Connection, ticker: str) -> bool:
    """True when ANY user still watches ``ticker`` (M4 market-source rule).

    The market data source tracks the union of all users' watchlists, so a
    ticker may be dropped from the source only when no user watches it.
    """
    row = conn.execute(
        "SELECT 1 FROM watchlist WHERE ticker = ? LIMIT 1", (ticker,)
    ).fetchone()
    return row is not None


def apply_watchlist_change_on_conn(
    conn: sqlite3.Connection,
    ticker: str,
    action: str,
    user_id: str = "default",
) -> dict:
    """Apply a watchlist add or remove operation on an open SQLite connection.

    Validates and executes the watchlist mutation against the provided connection.
    All validation failures return a dict with status="failed" and an "error" key
    — this function never raises on validation errors.

    Does NOT commit — the caller owns the transaction boundary and must commit
    (or roll back). This allows callers to batch watchlist changes with other
    writes (trades, chat messages) atomically in a single transaction. This
    helper also does not touch the live market data source; callers must sync
    applied changes via ``sync_market_source`` (see its docstring for timing).

    Args:
        conn: An open SQLite connection (caller manages lifecycle and commit).
        ticker: Ticker symbol (normalized with .strip().upper() internally).
        action: "add" or "remove" (normalized to lowercase internally).
        user_id: Whose watchlist to mutate (M4 — defaults to the anonymous user).

    Returns:
        On add success:    {"status": "added",   "ticker": T, "action": "add"}
        On remove success: {"status": "removed", "ticker": T, "action": "remove"}
        On failure:        {"status": "failed",  "ticker": T, "error": "message"}
    """
    ticker = ticker.strip().upper()
    action = action.lower()

    if not ticker:
        return {"status": "failed", "ticker": ticker, "error": "Ticker must not be empty"}

    if len(ticker) > 10:
        return {"status": "failed", "ticker": ticker, "error": "Ticker must be 10 characters or fewer"}

    if action == "add":
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, ticker, datetime.now(timezone.utc).isoformat()),
        )
        return {"status": "added", "ticker": ticker, "action": "add"}

    if action == "remove":
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        )
        return {"status": "removed", "ticker": ticker, "action": "remove"}

    return {"status": "failed", "ticker": ticker, "error": "Action must be 'add' or 'remove'"}


async def sync_market_source(request: Request, ticker: str, action: str) -> None:
    """Best-effort sync of a committed watchlist change to the market data source.

    Looks up the ``MarketDataSource`` stored at ``request.app.state.market_source``
    (set in main.py's lifespan) and calls ``add_ticker``/``remove_ticker`` so newly
    watched tickers start producing prices (SSE, trades) and removed tickers stop
    simulating/streaming.

    Consistency model: the database is the source of truth. Removals must be
    called AFTER the DB change is committed. Adds are normally synced after
    the commit too, but the chat flow deliberately syncs adds BEFORE its
    commit (add_ticker seeds the price cache so same-turn trades on a
    just-added ticker can execute) and reconciles the source on rollback. If
    the source is absent (e.g. unit tests without a market source on
    app.state) or the source call raises, the DB change stands, the error is
    logged, and the source re-syncs from the DB watchlist on the next app
    startup — divergence self-heals.

    Args:
        request: Current request (used to reach ``app.state.market_source``).
        ticker: Normalized ticker symbol.
        action: "add" or "remove".
    """
    source = getattr(request.app.state, "market_source", None)
    if source is None:
        return
    try:
        if action == "add":
            await source.add_ticker(ticker)
        elif action == "remove":
            await source.remove_ticker(ticker)
    except Exception:
        logger.exception(
            "Market source %s failed for %s (DB change stands; source re-syncs on restart)",
            action,
            ticker,
        )


def create_watchlist_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the watchlist APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache populated by the market data source.
        db_path: Path to the SQLite database file.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

    @router.get("/")
    async def get_watchlist(request: Request) -> dict:
        """Return all watchlist tickers enriched with live price data from cache."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at ASC",
                (user_id,),
            ).fetchall()

            tickers = []
            for row in rows:
                ticker: str = row["ticker"]
                added_at: str = row["added_at"]
                update = price_cache.get(ticker)
                tickers.append(
                    {
                        "ticker": ticker,
                        "added_at": added_at,
                        "price": update.price if update else None,
                        "change_percent": update.change_percent if update else None,
                        "direction": update.direction if update else None,
                        "day_change_percent": update.day_change_percent if update else None,
                    }
                )

            return {"tickers": tickers}
        finally:
            conn.close()

    @router.post("/")
    async def add_ticker(body: AddTickerRequest, request: Request) -> dict:
        """Add a ticker to the watchlist and register it with the market data source.

        Idempotent — if the ticker already exists, returns 200 without error.
        Ticker is normalized to uppercase.
        Returns HTTP 400 for invalid tickers (empty or longer than 10 characters).

        The DB row is committed first, then the live market data source starts
        tracking the ticker so it immediately gets prices (SSE stream, trades).
        If the source call fails the DB change stands (see ``sync_market_source``).
        """
        ticker = body.ticker.strip().upper()

        if not ticker:
            return JSONResponse(status_code=400, content={"error": "Ticker must not be empty"})

        if len(ticker) > 10:
            return JSONResponse(status_code=400, content={"error": "Ticker must be 10 characters or fewer"})

        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, ticker, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        await sync_market_source(request, ticker, "add")

        return {"status": "ok", "ticker": ticker}

    @router.delete("/{ticker}")
    async def remove_ticker(ticker: str, request: Request) -> dict:
        """Remove a ticker from the caller's watchlist.

        Idempotent — returns 200 even if the ticker was not in the watchlist.
        Ticker is normalized to uppercase.

        The DB row is deleted and committed first. The live market data
        source stops simulating/streaming the ticker ONLY when no user
        watches it anymore (M4 — the source tracks the union of all users'
        watchlists). If the source call fails the DB change stands (see
        ``sync_market_source``).
        """
        ticker = ticker.strip().upper()

        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
                (user_id, ticker),
            )
            conn.commit()
            still_watched = ticker_watched_by_anyone(conn, ticker)
        finally:
            conn.close()

        if not still_watched:
            await sync_market_source(request, ticker, "remove")

        return {"status": "ok", "ticker": ticker}

    return router
