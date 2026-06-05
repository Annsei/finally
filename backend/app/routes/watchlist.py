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

from app.db.connection import get_conn
from app.market.cache import PriceCache

logger = logging.getLogger(__name__)


class AddTickerRequest(BaseModel):
    ticker: str


def apply_watchlist_change_on_conn(
    conn: sqlite3.Connection,
    ticker: str,
    action: str,
) -> dict:
    """Apply a watchlist add or remove operation on an open SQLite connection.

    Validates and executes the watchlist mutation against the provided connection.
    All validation failures return a dict with status="failed" and an "error" key
    — this function never raises on validation errors.

    Args:
        conn: An open SQLite connection (caller manages lifecycle).
        ticker: Ticker symbol (normalized with .strip().upper() internally).
        action: "add" or "remove" (normalized to lowercase internally).

    Returns:
        On add success:    {"status": "added",   "ticker": T, "action": "add"}
        On remove success: {"status": "removed", "ticker": T, "action": "remove"}
        On failure:        {"status": "failed",  "ticker": T, "error": "message"}
    """
    ticker = ticker.strip().upper()
    action = action.lower()

    if not ticker:
        return {"status": "failed", "ticker": ticker, "error": "Ticker must not be empty"}

    if action == "add":
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, 'default', ?, ?)",
            (str(uuid.uuid4()), ticker, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"status": "added", "ticker": ticker, "action": "add"}

    if action == "remove":
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = 'default' AND ticker = ?",
            (ticker,),
        )
        conn.commit()
        return {"status": "removed", "ticker": ticker, "action": "remove"}

    return {"status": "failed", "ticker": ticker, "error": "Action must be 'add' or 'remove'"}


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
        conn = get_conn(db_path)
        try:
            rows = conn.execute(
                "SELECT ticker, added_at FROM watchlist WHERE user_id = 'default' ORDER BY added_at ASC"
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
                    }
                )

            return {"tickers": tickers}
        finally:
            conn.close()

    @router.post("/")
    async def add_ticker(body: AddTickerRequest, request: Request) -> dict:
        """Add a ticker to the watchlist.

        Idempotent — if the ticker already exists, returns 200 without error.
        Ticker is normalized to uppercase.
        Returns HTTP 400 for invalid tickers (empty or longer than 10 characters).
        """
        ticker = body.ticker.strip().upper()

        if not ticker:
            return JSONResponse(status_code=400, content={"error": "Ticker must not be empty"})

        if len(ticker) > 10:
            return JSONResponse(status_code=400, content={"error": "Ticker must be 10 characters or fewer"})

        conn = get_conn(db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, 'default', ?, ?)",
                (str(uuid.uuid4()), ticker, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        return {"status": "ok", "ticker": ticker}

    @router.delete("/{ticker}")
    async def remove_ticker(ticker: str, request: Request) -> dict:
        """Remove a ticker from the watchlist.

        Idempotent — returns 200 even if the ticker was not in the watchlist.
        Ticker is normalized to uppercase.
        """
        ticker = ticker.strip().upper()

        conn = get_conn(db_path)
        try:
            conn.execute(
                "DELETE FROM watchlist WHERE user_id = 'default' AND ticker = ?",
                (ticker,),
            )
            conn.commit()
        finally:
            conn.close()

        return {"status": "ok", "ticker": ticker}

    return router
