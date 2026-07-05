"""Market data API routes for FinAlly.

Provides:
- GET /api/market/history — recent 1-second OHLCV bars for a ticker, served
  from the PriceCache's in-memory ring buffer (~2h capacity). Used by the
  frontend to backfill charts before splicing in the live SSE stream.
- GET /api/market/events — recent market events (sudden >=1% single-tick
  moves) detected in the PriceCache funnel, newest first. Feeds the
  scrolling news ticker.

All routes are created via the factory function ``create_market_router`` which
closes over the shared ``PriceCache`` instance.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.market.cache import DEFAULT_HISTORY_CAPACITY, EVENT_BUFFER_SIZE, PriceCache

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 3600  # ~1h of 1-second bars
DEFAULT_EVENTS_LIMIT = 20


def create_market_router(price_cache: PriceCache) -> APIRouter:
    """Factory: build the market APIRouter with injected dependencies.

    Args:
        price_cache: Shared in-memory price cache (owns the OHLCV ring buffers).

    Returns:
        A configured FastAPI APIRouter ready to be registered with ``app.include_router``.
    """
    router = APIRouter(prefix="/api/market", tags=["market"])

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

    return router
