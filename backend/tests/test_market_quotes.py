"""Tests for GET /api/market/quotes (P1 §3.4).

The endpoint serves the full PriceCache snapshot — each quote is the
ticker's ``PriceUpdate.to_dict()`` payload (the exact SSE shape) plus a
``"sector"`` key from the active market universe — sorted ascending by
ticker. Covers shape, sorting, sector resolution (including unknown ->
"other"), the empty cache, and the CN universe (sectors + price-limit
fields flowing through ``to_dict``).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.market.seed_prices_cn import CN_UNIVERSE
from app.routes.market import create_market_router

PRICE_UPDATE_KEYS = {
    "ticker", "price", "previous_price", "timestamp", "change",
    "change_percent", "direction", "prev_close", "day_change",
    "day_change_percent", "day_high", "day_low", "volume", "bid", "ask",
    "asset_class",
}


async def _make_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def quotes_client():
    """Bare market-router app over an empty PriceCache (US universe default)."""
    price_cache = PriceCache()
    test_app = FastAPI()
    test_app.include_router(create_market_router(price_cache))
    async with await _make_client(test_app) as client:
        yield client, price_cache


@pytest.mark.asyncio
class TestMarketQuotesEndpoint:
    """GET /api/market/quotes response semantics."""

    async def test_empty_cache_returns_empty_list(self, quotes_client):
        client, _ = quotes_client
        response = await client.get("/api/market/quotes")
        assert response.status_code == 200
        assert response.json() == {"quotes": []}

    async def test_quote_is_price_update_to_dict_plus_sector(self, quotes_client):
        client, cache = quotes_client
        cache.update("AAPL", 190.0, timestamp=1_700_000_010.0)
        cache.update("AAPL", 191.5, timestamp=1_700_000_011.0, volume=250.0)

        response = await client.get("/api/market/quotes")
        assert response.status_code == 200
        quotes = response.json()["quotes"]
        assert len(quotes) == 1
        expected = cache.get("AAPL").to_dict()
        expected["sector"] = "tech"
        assert quotes[0] == expected
        assert set(quotes[0].keys()) == PRICE_UPDATE_KEYS | {"sector"}

    async def test_sorted_ascending_by_ticker(self, quotes_client):
        client, cache = quotes_client
        for ticker in ("NVDA", "AAPL", "ZZZZ", "JPM", "BTC"):
            cache.update(ticker, 100.0)

        response = await client.get("/api/market/quotes")
        tickers = [q["ticker"] for q in response.json()["quotes"]]
        assert tickers == ["AAPL", "BTC", "JPM", "NVDA", "ZZZZ"]

    async def test_sectors_from_us_universe_and_unknown_is_other(self, quotes_client):
        client, cache = quotes_client
        for ticker in ("AAPL", "JPM", "BTC", "ZZZZ"):
            cache.update(ticker, 100.0)

        response = await client.get("/api/market/quotes")
        sectors = {q["ticker"]: q["sector"] for q in response.json()["quotes"]}
        assert sectors == {
            "AAPL": "tech",
            "JPM": "financials",
            "BTC": "crypto",
            "ZZZZ": "other",
        }

    async def test_default_wiring_covers_full_seeded_universe(
        self, app_client, fake_market_source
    ):
        """Through the shared app fixture: one quote per seeded ticker."""
        response = await app_client.get("/api/market/quotes")
        assert response.status_code == 200
        quotes = response.json()["quotes"]
        assert [q["ticker"] for q in quotes] == sorted(SEED_PRICES)
        by_ticker = {q["ticker"]: q for q in quotes}
        assert by_ticker["AAPL"]["sector"] == "tech"
        assert by_ticker["V"]["sector"] == "financials"

    async def test_cn_universe_sectors_and_price_limits(self):
        """CN wiring: A-share sectors resolve and limit fields flow through."""
        cache = PriceCache(limit_pct_fn=CN_PROFILE.price_limit_pct)
        for ticker, price in CN_UNIVERSE.seed_prices.items():
            cache.update(ticker, price)

        test_app = FastAPI()
        test_app.include_router(
            create_market_router(cache, universe=CN_UNIVERSE)
        )
        async with await _make_client(test_app) as client:
            response = await client.get("/api/market/quotes")

        assert response.status_code == 200
        quotes = response.json()["quotes"]
        assert [q["ticker"] for q in quotes] == sorted(CN_UNIVERSE.seed_prices)
        by_ticker = {q["ticker"]: q for q in quotes}
        assert by_ticker["600519"]["sector"] == "白酒"
        assert by_ticker["300750"]["sector"] == "新能源"
        # CN quotes carry the daily price-limit band (main board ±10%).
        maotai = by_ticker["600519"]
        assert maotai["limit_up"] == round(maotai["prev_close"] * 1.10, 2)
        assert maotai["limit_down"] == round(maotai["prev_close"] * 0.90, 2)
