"""Pytest configuration and fixtures."""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def app_client(tmp_path, monkeypatch):
    """FastAPI test client with isolated temp SQLite DB and seeded price cache.

    Creates a fresh FastAPI app instance per test to avoid route accumulation
    on the module-level app singleton (which registers routers inside lifespan).
    Initializes the DB and seeds the price cache so trade tests have prices.
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    from app.db.connection import init_db
    from app.market import PriceCache, create_stream_router
    from app.market.seed_prices import SEED_PRICES
    from app.routes.health import router as health_router
    from app.routes.portfolio import create_portfolio_router
    from app.routes.watchlist import create_watchlist_router

    init_db(db_file)

    price_cache = PriceCache()
    # Seed test prices for all default tickers so trade tests can get prices
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(health_router)
    test_app.include_router(create_stream_router(price_cache))
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_watchlist_router(price_cache, db_file))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client
