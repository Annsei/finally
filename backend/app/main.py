"""FinAlly FastAPI application entry point.

Wires together:
- Market data source (simulator or Massive API) with PriceCache
- SQLite database initialization
- All API routers (SSE streaming, health, portfolio, watchlist)
- Static file serving (Next.js export — conditional on build existing)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.connection import get_conn, init_db
from app.market import PriceCache, create_market_data_source, create_stream_router
from app.market.seed_prices import SEED_PRICES

logger = logging.getLogger(__name__)


async def _snapshot_loop(price_cache: PriceCache, db_path: str, interval: int = 30) -> None:
    """Background task: record a portfolio snapshot every ``interval`` seconds.

    Runs indefinitely until cancelled via ``asyncio.CancelledError``.
    """
    while True:
        try:
            conn = get_conn(db_path)
            try:
                from app.routes.portfolio import _record_snapshot
                _record_snapshot(conn, price_cache)
            finally:
                conn.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Snapshot loop error — will retry in %ds", interval)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: start/stop market data and initialize DB."""
    db_path = os.getenv("DB_PATH", "db/finally.db")

    logger.info("FinAlly startup: initializing database at %s", db_path)
    init_db(db_path)

    logger.info("FinAlly startup: creating price cache and market data source")
    price_cache = PriceCache()
    source = create_market_data_source(price_cache)

    # Start market data with tickers from DB watchlist, falling back to SEED_PRICES
    conn = get_conn(db_path)
    rows = conn.execute("SELECT ticker FROM watchlist WHERE user_id = 'default'").fetchall()
    tickers = [row["ticker"] for row in rows]
    conn.close()
    if not tickers:
        tickers = list(SEED_PRICES.keys())
    await source.start(tickers)
    logger.info("FinAlly startup: market data source started with %d tickers", len(tickers))

    # Store on app.state for access in request handlers
    app.state.price_cache = price_cache
    app.state.market_source = source

    # Register routers that depend on price_cache inside lifespan (factory pattern)
    app.include_router(create_stream_router(price_cache))

    # Portfolio router
    from app.routes.portfolio import create_portfolio_router
    portfolio_router = create_portfolio_router(price_cache, db_path)
    app.include_router(portfolio_router)

    # Watchlist router
    from app.routes.watchlist import create_watchlist_router
    watchlist_router = create_watchlist_router(price_cache, db_path)
    app.include_router(watchlist_router)

    # Start background portfolio snapshot task (every 30 seconds)
    snapshot_task = asyncio.create_task(_snapshot_loop(price_cache, db_path))
    app.state.snapshot_task = snapshot_task
    logger.info("FinAlly startup: portfolio snapshot background task started")

    yield

    logger.info("FinAlly shutdown: cancelling snapshot task")
    snapshot_task.cancel()
    try:
        await snapshot_task
    except asyncio.CancelledError:
        pass

    logger.info("FinAlly shutdown: stopping market data source")
    await source.stop()
    logger.info("FinAlly shutdown: complete")


# Create FastAPI application
app = FastAPI(
    title="FinAlly",
    description="AI-powered trading workstation",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers that have no dependencies (can be registered at import time)
from app.routes.health import router as health_router  # noqa: E402

app.include_router(health_router)

# Mount static files LAST — must not shadow /api/* routes
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    logger.info("Serving static files from %s", static_dir)
