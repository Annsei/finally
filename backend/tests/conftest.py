"""Pytest configuration and fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market import MarketDataSource, PriceCache


class FakeMarketSource(MarketDataSource):
    """In-memory MarketDataSource test double that records add/remove calls.

    Installed on ``app.state.market_source`` by the client fixtures (mirroring
    main.py's lifespan wiring) so tests can assert that routes sync watchlist
    changes to the market data source.

    When a ``price_cache`` is attached (the client fixtures attach the app's
    cache), the fake mirrors the real sources' cache semantics: ``add_ticker``
    seeds a price for a brand-new ticker immediately (the behavior the chat
    add-then-trade flow relies on) and ``remove_ticker`` evicts the ticker
    from the cache.
    """

    SEED_PRICE = 100.0

    def __init__(self, price_cache: PriceCache | None = None) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []
        self._tickers: list[str] = []
        self.price_cache = price_cache

    async def start(self, tickers: list[str]) -> None:
        self._tickers = list(tickers)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        self.added.append(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
        if self.price_cache is not None and self.price_cache.get(ticker) is None:
            self.price_cache.update(ticker, self.SEED_PRICE)

    async def remove_ticker(self, ticker: str) -> None:
        self.removed.append(ticker)
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        if self.price_cache is not None:
            self.price_cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)


@pytest.fixture
def fake_market_source():
    """Fake market data source; request alongside app_client/chat_client to assert sync calls."""
    return FakeMarketSource()


@pytest_asyncio.fixture
async def app_client(tmp_path, monkeypatch, fake_market_source):
    """FastAPI test client with isolated temp SQLite DB and seeded price cache.

    Creates a fresh FastAPI app instance per test to avoid route accumulation
    on the module-level app singleton (which registers routers inside lifespan).
    Initializes the DB, seeds the price cache so trade tests have prices, and
    installs a FakeMarketSource on app.state (as main.py's lifespan does).
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

    # Attach the app's cache so the fake source seeds prices on add_ticker
    # (mirrors SimulatorDataSource/MassiveDataSource behavior).
    fake_market_source.price_cache = price_cache

    test_app = FastAPI()
    test_app.state.market_source = fake_market_source
    test_app.include_router(health_router)
    test_app.include_router(create_stream_router(price_cache))
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_watchlist_router(price_cache, db_file))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def chat_client(tmp_path, monkeypatch, fake_market_source):
    """FastAPI test client with all routers registered and LLM_MOCK=true.

    Extends app_client with the chat router and sets LLM_MOCK=true so that
    chat tests never make real network calls to OpenRouter.
    Includes all five routers: health, stream, portfolio, watchlist, chat.
    Installs a FakeMarketSource on app.state (as main.py's lifespan does).
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")

    from app.db.connection import init_db
    from app.market import PriceCache, create_stream_router
    from app.market.seed_prices import SEED_PRICES
    from app.routes.chat import create_chat_router
    from app.routes.health import router as health_router
    from app.routes.portfolio import create_portfolio_router
    from app.routes.watchlist import create_watchlist_router

    init_db(db_file)

    price_cache = PriceCache()
    # Seed test prices for all default tickers so mock AAPL buy has a price
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    # Attach the app's cache so the fake source seeds prices on add_ticker
    # (mirrors SimulatorDataSource/MassiveDataSource behavior).
    fake_market_source.price_cache = price_cache

    test_app = FastAPI()
    test_app.state.market_source = fake_market_source
    test_app.include_router(health_router)
    test_app.include_router(create_stream_router(price_cache))
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_watchlist_router(price_cache, db_file))
    test_app.include_router(create_chat_router(price_cache, db_file))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client
