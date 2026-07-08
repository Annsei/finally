"""Market data API routes for FinAlly.

Provides:
- GET /api/market/history — recent 1-second OHLCV bars for a ticker, served
  from the PriceCache's in-memory ring buffer (~2h capacity). Used by the
  frontend to backfill charts before splicing in the live SSE stream.
- GET /api/market/events — recent market events (sudden >=1% single-tick
  moves) detected in the PriceCache funnel, newest first. Feeds the
  scrolling news ticker.
- GET /api/market/events/archive — the durable market-event archive (P1
  §3.3): reads the ``market_events`` table kept fresh by the background
  persist loop, with optional ticker filter and ``before`` cursor pagination.
- GET /api/market/quotes — full PriceCache snapshot with sectors (P1 §3.4),
  ascending by ticker. Seeds the /market page grid and heatmap.
- GET /api/market/session — current trading-session state from the
  SessionClock (M3.1). Drives the Header session badge and lets the frontend
  render open/closed state and a countdown to the next transition.

All routes are created via the factory function ``create_market_router`` which
closes over the shared ``PriceCache`` instance and the ``SessionClock``.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.connection import get_conn
from app.market.cache import DEFAULT_HISTORY_CAPACITY, EVENT_BUFFER_SIZE, PriceCache
from app.market.session import SessionClock
from app.market.universe import US_UNIVERSE, MarketUniverse

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 3600  # ~1h of 1-second bars
DEFAULT_EVENTS_LIMIT = 20
DEFAULT_ARCHIVE_LIMIT = 50  # P1 §3.3: default page size for /events/archive
MAX_ARCHIVE_LIMIT = 200  # P1 §3.3: hard cap for /events/archive


def create_market_router(
    price_cache: PriceCache,
    session_clock: SessionClock | None = None,
    db_path: str | None = None,
    universe: MarketUniverse | None = None,
) -> APIRouter:
    """Factory: build the market APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache (owns the OHLCV ring buffers).
        session_clock: Session clock backing GET /session (M3.1). When omitted
            a fresh 24/7 clock is used (always open, next_transition_at null)
            so the endpoint contract holds in tests and legacy wiring.
        db_path: SQLite path backing GET /events/archive (P1 §3.3). main.py
            passes it explicitly; when omitted (legacy wiring) the archive
            endpoint falls back to the DB_PATH environment variable at request
            time — the same database every other component uses.
        universe: Active market universe (P1 §3.4) — supplies the sector for
            each quote via ``sector_for`` (unknown tickers -> "other"). When
            omitted the US universe is used, matching the pre-profile lookups.

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    if session_clock is None:
        session_clock = SessionClock()  # 24/7 mode
    if universe is None:
        universe = US_UNIVERSE

    router = APIRouter(prefix="/api/market", tags=["market"])

    @router.get("/session")
    async def get_session() -> dict:
        """Return the current trading-session state (M3.1).

        Response shape (contract fixed — frontend built in parallel):
            {"state": "open" | "closed",
             "session_id": int,           # starts at 1, bumps on each reopen
             "state_since": float,        # Unix seconds current state began
             "next_transition_at": float | null,  # null in 24/7 mode
             "now": float}                # server clock, for countdowns
        """
        return session_clock.snapshot()

    @router.get("/history")
    async def get_history(
        request: Request, ticker: str | None = None, limit: str | None = None
    ) -> dict:
        """Return a ticker's recent 1-second OHLCV bars, ascending by time.

        Query params:
            ticker: required ticker symbol (uppercase-normalized). Missing or
                empty returns HTTP 400 with ``{"error": "message"}``.
            limit: maximum number of most-recent bars to return. Defaults to
                3600 and is clamped to the range 1..7200. Non-integer values
                return HTTP 400 with ``{"error": "message"}``.

        Unknown/uncached tickers return 200 with an empty bars list.
        """
        if ticker is None or not ticker.strip():
            return JSONResponse(
                status_code=400, content={"error": "ticker query parameter is required"}
            )
        ticker_value = ticker.strip().upper()

        if limit is None:
            limit_value = DEFAULT_HISTORY_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "limit must be an integer"}
                )
        limit_value = max(1, min(DEFAULT_HISTORY_CAPACITY, limit_value))

        return {
            "ticker": ticker_value,
            "bars": price_cache.get_history(ticker_value, limit=limit_value),
        }

    @router.get("/events")
    async def get_events(request: Request, limit: str | None = None) -> dict:
        """Return recent market events (sudden price moves), newest first.

        Query params:
            limit: maximum number of newest events to return. Defaults to 20
                and is clamped to the range 1..100. Non-integer values return
                HTTP 400 with ``{"error": "message"}``.

        Returns 200 with ``{"events": [...]}`` — an empty list when no events
        have been detected yet.
        """
        if limit is None:
            limit_value = DEFAULT_EVENTS_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "limit must be an integer"}
                )
        limit_value = max(1, min(EVENT_BUFFER_SIZE, limit_value))

        return {
            "events": [event.to_dict() for event in price_cache.get_events(limit=limit_value)],
        }

    @router.get("/events/archive")
    async def get_events_archive(
        request: Request,
        ticker: str | None = None,
        limit: str | None = None,
        before: str | None = None,
    ) -> dict:
        """Return archived market events from SQLite, newest first (P1 §3.3).

        Reads the ``market_events`` table (kept fresh by the background
        persist loop), so unlike GET /events this survives ring-buffer
        eviction and restarts. No auth — market-level data.

        Query params:
            ticker: optional ticker filter (uppercase-normalized exact
                match). Blank values are treated as absent.
            limit: page size. Defaults to 50 and is clamped to 1..200.
                Non-integer values return HTTP 400 with ``{"error": ...}``.
            before: optional float Unix timestamp cursor — only events with
                ``timestamp`` STRICTLY below it are returned. Pass the oldest
                timestamp of the previous page to paginate. Non-numeric
                values return HTTP 400 with ``{"error": ...}``.

        Returns 200 with ``{"events": [...], "has_more": bool}`` —
        ``has_more`` is true when more events exist past this page.
        """
        ticker_value = ticker.strip().upper() if ticker is not None and ticker.strip() else None

        if limit is None:
            limit_value = DEFAULT_ARCHIVE_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "limit must be an integer"}
                )
        limit_value = max(1, min(MAX_ARCHIVE_LIMIT, limit_value))

        before_value: float | None = None
        if before is not None:
            try:
                before_value = float(before)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "before must be a number"}
                )

        query = (
            "SELECT id, ticker, headline, narrative, change_percent, direction, timestamp "
            "FROM market_events"
        )
        conditions: list[str] = []
        params: list[object] = []
        if ticker_value is not None:
            conditions.append("ticker = ?")
            params.append(ticker_value)
        if before_value is not None:
            conditions.append("timestamp < ?")
            params.append(before_value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        # Fetch one extra row past the page to compute has_more cheaply.
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit_value + 1)

        resolved_db_path = db_path if db_path is not None else os.getenv("DB_PATH", "db/finally.db")
        conn = get_conn(resolved_db_path)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        has_more = len(rows) > limit_value
        return {
            "events": [
                {
                    "id": row["id"],
                    "ticker": row["ticker"],
                    "headline": row["headline"],
                    "narrative": row["narrative"],
                    "change_percent": row["change_percent"],
                    "direction": row["direction"],
                    "timestamp": row["timestamp"],
                }
                for row in rows[:limit_value]
            ],
            "has_more": has_more,
        }

    @router.get("/quotes")
    async def get_quotes(request: Request) -> dict:
        """Return the full PriceCache snapshot with sectors (P1 §3.4).

        Every ticker currently in the cache, ascending by ticker for
        deterministic ordering. Each quote is the ticker's
        ``PriceUpdate.to_dict()`` payload (the exact SSE shape, including
        limit_up/limit_down when the market carries price limits) plus a
        ``"sector"`` key from the active universe (unknown/user-added
        tickers -> "other"). No auth — market-level data.

        Returns 200 with ``{"quotes": [...]}`` — an empty list when the
        cache holds no tickers yet.
        """
        snapshot = price_cache.get_all()
        quotes = []
        for symbol in sorted(snapshot):
            payload = snapshot[symbol].to_dict()
            payload["sector"] = universe.sector_for(symbol)
            quotes.append(payload)
        return {"quotes": quotes}

    return router
