"""History data API routes for FinAlly (D1 contract §2) — cookie identity.

Endpoints (all under /api/market/history — the path-distinct siblings of the
legacy GET /api/market/history ring-buffer endpoint):

- POST ``/sync`` ``{source?: "auto"|"sample"|<real source>, tickers?: [str],
  years?: int 1..10 (default 3)}`` — fetch + upsert daily bars.
  ``auto`` tries the market's real provider (us: yfinance, cn: akshare) and
  falls back to ``sample`` per ticker, annotating the fallback row's
  ``error``. Runs in ``asyncio.to_thread`` (a real sync can take seconds to
  minutes). Bearer-authenticated calls get 403 (the keys.py red line — a
  leaked key must not be able to hammer real market-data hosts), and calls
  closer together than ``min_sync_interval_seconds`` (10s) get 429.
  → ``{"results": [{ticker, source, bars, error?}], "total_bars": int}``.
- GET ``/daily?ticker=&limit=`` — stored bars ascending; limit default 260
  clamped 1..2600 → ``{"ticker", "bars": [{date, open, high, low, close,
  volume}], "source", "coverage": {from, to, count}}`` (``source`` = the
  newest stored bar's source; whole-table coverage).
- GET ``/coverage`` → ``{"coverage": [{ticker, from, to, count, source}],
  "market"}`` ascending by ticker — the /market page data-status card.

The GET endpoints are market-level reads (no auth), matching the other
/api/market routes; the Guest identity can sync (§5 — Guest 可用).

CORE INVARIANT (contract §0): real network fetches happen ONLY inside a
user-triggered sync. Router construction never imports yfinance/akshare
(providers import lazily inside fetch); tests inject fake providers.

Factory ``create_history_router(db_path, profile=..., providers=...,
min_sync_interval_seconds=...)`` mirrors the other routers.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.connection import get_conn
from app.market.history import (
    REAL_SOURCE_BY_MARKET,
    HistoryProvider,
    build_default_providers,
    coverage_rows,
    load_recent_daily_bars,
    sync_daily_bars,
    ticker_coverage,
)
from app.market.profiles import MarketProfile

logger = logging.getLogger(__name__)

DEFAULT_DAILY_LIMIT = 260  # ~1 trading year
MAX_DAILY_LIMIT = 2600  # ~10 trading years
DEFAULT_SYNC_YEARS = 3
MIN_SYNC_YEARS, MAX_SYNC_YEARS = 1, 10
DEFAULT_SYNC_INTERVAL_SECONDS = 10.0


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _bearer_rejection(request: Request) -> JSONResponse | None:
    """403 for any Bearer-authenticated sync call (keys.py's red-line pattern).

    Belt and braces like routes/keys.py: both the gateway-authenticated
    marker (``request.state.api_key_id``) and a raw Authorization: Bearer
    header are rejected, so a leaked API key can never trigger real
    market-data fetches even in an app that mounts this router without the
    gateway middleware.
    """
    if getattr(request.state, "api_key_id", None) is not None:
        return _error(403, "API keys cannot trigger a history sync")
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        return _error(403, "API keys cannot trigger a history sync")
    return None


async def _read_json_object(request: Request) -> dict | None:
    """Parse the request body as a JSON object; empty body -> {} (all defaults)."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def create_history_router(
    db_path: str,
    profile: MarketProfile | None = None,
    providers: dict[str, HistoryProvider] | None = None,
    min_sync_interval_seconds: float = DEFAULT_SYNC_INTERVAL_SECONDS,
) -> APIRouter:
    """Factory: build the /api/market/history APIRouter (contract §2).

    Args:
        db_path: SQLite path — daily_bars lives in the same database as
            everything else.
        profile: Active market profile (CN-1). Selects the market key
            ("us"/"cn" — the daily_bars partition), the default sync ticker
            set (the profile's default watchlist), and which real provider
            ``auto`` tries. None keeps the US defaults.
        providers: Source-name -> provider map. None builds the standard
            sample/yfinance/akshare set (lazy imports — construction never
            touches the optional packages). Tests inject fakes here so no
            test path can reach a real market-data host.
        min_sync_interval_seconds: 429 throttle between sync calls
            (contract §2 — the guard against hammering real providers).
    """
    market = profile.key if profile is not None else "us"
    default_tickers = (
        list(profile.universe.default_watchlist)
        if profile is not None
        else None
    )
    if default_tickers is None:
        from app.market.seed_prices import DEFAULT_WATCHLIST

        default_tickers = list(DEFAULT_WATCHLIST)
    provider_map = providers if providers is not None else build_default_providers(market)
    real_source = REAL_SOURCE_BY_MARKET.get(market, "yfinance")
    allowed_sources = {"auto", "sample", real_source}

    router = APIRouter(prefix="/api/market/history", tags=["history"])

    # Throttle state — closure-scoped, one window per router instance.
    last_sync: dict[str, float | None] = {"at": None}

    @router.post("/sync")
    async def sync_history(request: Request):
        """Fetch + upsert daily bars for a set of tickers (contract §2)."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection

        payload = await _read_json_object(request)
        if payload is None:
            return _error(400, "Invalid JSON body")

        source = payload.get("source")
        source = "auto" if source is None else str(source).strip().lower()
        if source not in allowed_sources:
            return _error(
                400,
                f"source must be one of {sorted(allowed_sources)} for the "
                f"'{market}' market",
            )

        years = payload.get("years")
        if years is None:
            years_value = DEFAULT_SYNC_YEARS
        else:
            if isinstance(years, bool) or not isinstance(years, int):
                return _error(400, "years must be an integer between 1 and 10")
            years_value = years
        if not MIN_SYNC_YEARS <= years_value <= MAX_SYNC_YEARS:
            return _error(400, "years must be an integer between 1 and 10")

        tickers = payload.get("tickers")
        if tickers is None:
            ticker_list = list(default_tickers)
        else:
            if not isinstance(tickers, list) or not tickers:
                return _error(400, "tickers must be a non-empty list of symbols")
            ticker_list = []
            for item in tickers:
                if not isinstance(item, str) or not item.strip():
                    return _error(400, "tickers must be a non-empty list of symbols")
                ticker_list.append(item.strip().upper())

        # 429 throttle (contract §2): consecutive syncs closer than the
        # window are rejected — the guard against hammering real providers.
        # Stamped only for requests that reach execution.
        now = time.monotonic()
        if (
            last_sync["at"] is not None
            and now - last_sync["at"] < min_sync_interval_seconds
        ):
            return _error(
                429, "History sync is rate limited — wait a few seconds and retry"
            )
        last_sync["at"] = now

        def _run_sync() -> dict:
            conn = get_conn(db_path)
            try:
                return sync_daily_bars(
                    conn,
                    market=market,
                    tickers=ticker_list,
                    source=source,
                    years=years_value,
                    providers=provider_map,
                )
            finally:
                conn.close()

        result = await asyncio.to_thread(_run_sync)
        logger.info(
            "History sync (%s, source=%s): %d tickers, %d bars",
            market,
            source,
            len(result["results"]),
            result["total_bars"],
        )
        return result

    @router.get("/daily")
    async def get_daily(
        request: Request, ticker: str | None = None, limit: str | None = None
    ):
        """Stored daily bars for one ticker, ascending (contract §2)."""
        if ticker is None or not ticker.strip():
            return _error(400, "ticker query parameter is required")
        ticker_value = ticker.strip().upper()

        if limit is None:
            limit_value = DEFAULT_DAILY_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return _error(400, "limit must be an integer")
        limit_value = max(1, min(MAX_DAILY_LIMIT, limit_value))

        conn = get_conn(db_path)
        try:
            rows = load_recent_daily_bars(conn, market, ticker_value, limit_value)
            coverage = ticker_coverage(conn, market, ticker_value)
        finally:
            conn.close()

        return {
            "ticker": ticker_value,
            "bars": [
                {
                    "date": row["date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
                for row in rows
            ],
            # The newest stored bar's source; null when nothing is stored.
            "source": rows[-1]["source"] if rows else None,
            "coverage": coverage,
        }

    @router.get("/coverage")
    async def get_coverage(request: Request):
        """Per-ticker stored-bar coverage for the active market (§2)."""
        conn = get_conn(db_path)
        try:
            rows = coverage_rows(conn, market)
        finally:
            conn.close()
        return {"coverage": rows, "market": market}

    return router
