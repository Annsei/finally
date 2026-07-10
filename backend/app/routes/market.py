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
- GET /api/market/sentiment — the three-axis market sentiment index (P4 §1)
  computed from the PriceCache snapshot + 1-second ring buffers. Feeds the
  /market page gauge and the AI context line.
- GET /api/market/correlation — NxN Pearson correlation of 1-minute log
  returns over a recent window (P4 §2), tickers grouped by sector. Feeds the
  /market page heatmap.

All routes are created via the factory function ``create_market_router`` which
closes over the shared ``PriceCache`` instance and the ``SessionClock``.
"""

from __future__ import annotations

import logging
import math
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.connection import get_conn
from app.indicators import aggregate_minute_bars
from app.market.cache import DEFAULT_HISTORY_CAPACITY, EVENT_BUFFER_SIZE, PriceCache
from app.market.sentiment import compute_market_sentiment
from app.market.session import SessionClock
from app.market.universe import US_UNIVERSE, MarketUniverse

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 3600  # ~1h of 1-second bars
DEFAULT_EVENTS_LIMIT = 20
DEFAULT_ARCHIVE_LIMIT = 50  # P1 §3.3: default page size for /events/archive
MAX_ARCHIVE_LIMIT = 200  # P1 §3.3: hard cap for /events/archive

# P4 §2: correlation window (minutes of 1-minute bars) and eligibility gate.
DEFAULT_CORRELATION_MINUTES = 30
MIN_CORRELATION_MINUTES = 5
MAX_CORRELATION_MINUTES = 120
MIN_CORRELATION_BARS = 10  # tickers with fewer completed bars are excluded


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation of two equal-length series.

    Returns 0.0 when either series is constant (zero variance — the P4 §2
    "恒价分母 0" rule) or shorter than 2 points. Never raises.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def compute_correlation_matrix(
    price_cache: PriceCache, universe: MarketUniverse, minutes: int
) -> dict:
    """Pearson correlation of 1-minute log returns over the last ``minutes`` (P4 §2).

    For every ticker in the cache the 1-second ring buffer is aggregated to
    COMPLETED one-minute bars (:func:`aggregate_minute_bars`); the window is
    anchored on the newest completed bar across the board and covers the
    last ``minutes`` minutes. Tickers with fewer than
    :data:`MIN_CORRELATION_BARS` bars inside the window are excluded (early
    session — not enough signal). Log returns are keyed by bar time and each
    pair correlates over its common timestamps, so tickers with gaps never
    misalign.

    Output tickers are grouped by sector (sorted by ``(sector, ticker)``) so
    sector blocks sit together on the heatmap. Self-correlation is exactly
    1.0; constant-price series (zero variance) correlate 0.0. Fewer than two
    eligible tickers -> ``{"tickers": [], "sectors": {}, "matrix": [],
    "minutes": minutes}``.
    """
    snapshot = price_cache.get_all()
    bars_by_ticker: dict[str, list[dict]] = {}
    for ticker in snapshot:
        bars = aggregate_minute_bars(price_cache.get_history(ticker))
        if bars:
            bars_by_ticker[ticker] = bars
    empty = {"tickers": [], "sectors": {}, "matrix": [], "minutes": minutes}
    if not bars_by_ticker:
        return empty

    anchor = max(bars[-1]["time"] for bars in bars_by_ticker.values())
    cutoff = anchor - minutes * 60
    returns_by_ticker: dict[str, dict[int, float]] = {}
    for ticker, bars in bars_by_ticker.items():
        window = [bar for bar in bars if bar["time"] > cutoff]
        if len(window) < MIN_CORRELATION_BARS:
            continue
        returns: dict[int, float] = {}
        prev_close: float | None = None
        for bar in window:
            close = bar["close"]
            if prev_close is not None and prev_close > 0 and close > 0:
                returns[bar["time"]] = math.log(close / prev_close)
            prev_close = close
        returns_by_ticker[ticker] = returns

    if len(returns_by_ticker) < 2:
        return empty

    tickers = sorted(returns_by_ticker, key=lambda t: (universe.sector_for(t), t))
    matrix: list[list[float]] = []
    for a in tickers:
        row: list[float] = []
        for b in tickers:
            if a == b:
                row.append(1.0)
                continue
            common = sorted(set(returns_by_ticker[a]) & set(returns_by_ticker[b]))
            r = _pearson(
                [returns_by_ticker[a][t] for t in common],
                [returns_by_ticker[b][t] for t in common],
            )
            # ``+ 0.0`` normalizes the -0.0 that round() can produce.
            row.append(round(r, 2) + 0.0)
        matrix.append(row)
    return {
        "tickers": tickers,
        "sectors": {t: universe.sector_for(t) for t in tickers},
        "matrix": matrix,
        "minutes": minutes,
    }


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

    @router.get("/sentiment")
    async def get_sentiment(request: Request) -> dict:
        """Return the three-axis market sentiment index (P4 §1). No auth.

        Response shape (contract fixed — frontend built in parallel):
            {"score": int, "label": "frozen"|"cool"|"neutral"|"active"|"hot",
             "axes": {"breadth": n, "volatility": n, "volume": n},
             "sample_size": int}

        Fewer than 2 tickers in the cache reads neutral (all axes 50).
        """
        return compute_market_sentiment(price_cache)

    @router.get("/correlation")
    async def get_correlation(request: Request, minutes: str | None = None) -> dict:
        """Return the sector-grouped correlation matrix (P4 §2). No auth.

        Query params:
            minutes: window of 1-minute bars to correlate over. Defaults to
                30 and is clamped to the range 5..120. Non-integer values
                return HTTP 400 with ``{"error": "message"}``.

        Response shape (contract fixed — frontend built in parallel):
            {"tickers": [str], "sectors": {ticker: sector},
             "matrix": [[float 2dp]], "minutes": int}

        Fewer than two tickers with >= 10 completed bars inside the window
        (early session) returns the empty shape with ``tickers == []``.
        """
        if minutes is None:
            minutes_value = DEFAULT_CORRELATION_MINUTES
        else:
            try:
                minutes_value = int(minutes)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "minutes must be an integer"}
                )
        minutes_value = max(
            MIN_CORRELATION_MINUTES, min(MAX_CORRELATION_MINUTES, minutes_value)
        )
        return compute_correlation_matrix(price_cache, universe, minutes_value)

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
