"""FinAlly FastAPI application entry point.

Wires together:
- Market data source (simulator or Massive API) with PriceCache
- SQLite database initialization
- All API routers (SSE streaming, health, portfolio, watchlist)
- Static file serving (Next.js export — conditional on build existing)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.connection import init_db
from app.market import PriceCache, create_market_data_source, create_stream_router
from app.market.seed_prices import SEED_PRICES

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: start/stop market data and initialize DB."""
    logger.info("FinAlly startup: initializing database")
    init_db()

    logger.info("FinAlly startup: creating price cache and market data source")
    price_cache = PriceCache()
    source = create_market_data_source(price_cache)

    # Start market data with all default tickers
    tickers = list(SEED_PRICES.keys())
    await source.start(tickers)
    logger.info("FinAlly startup: market data source started with %d tickers", len(tickers))

    # Store on app.state for access in request handlers
    app.state.price_cache = price_cache
    app.state.market_source = source

    # Register routers that depend on price_cache inside lifespan (factory pattern)
    app.include_router(create_stream_router(price_cache))
    # Portfolio router (wave 2 — added when 01C/01D execute)
    # Watchlist router (wave 2 — added when 01C/01D execute)

    yield

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
