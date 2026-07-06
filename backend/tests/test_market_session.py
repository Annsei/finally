"""Tests for GET /api/market/session and trading-hours semantics (M3.1/M3.3)."""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market.session import SessionClock
from tests.conftest import FakeTime

SESSION_KEYS = {"state", "session_id", "state_since", "next_transition_at", "now"}


@pytest_asyncio.fixture
async def session_env(tmp_path, monkeypatch, fake_market_source):
    """(client, clock, fake_time) — full app wired to a 30s/10s session clock.

    Mirrors main.py's lifespan wiring (portfolio/orders/watchlist/chat/market
    routers sharing one SessionClock) with LLM_MOCK=true and injected time so
    tests drive transitions themselves via fake_time.advance() + clock.tick().
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")

    from app.db.connection import init_db
    from app.market import PriceCache
    from app.market.seed_prices import SEED_PRICES
    from app.routes.chat import create_chat_router
    from app.routes.market import create_market_router
    from app.routes.orders import create_orders_router
    from app.routes.portfolio import create_portfolio_router
    from app.routes.watchlist import create_watchlist_router

    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)
    fake_market_source.price_cache = price_cache

    fake_time = FakeTime()
    clock = SessionClock(30.0, 10.0, now=fake_time)

    test_app = FastAPI()
    test_app.state.market_source = fake_market_source
    test_app.state.session_clock = clock
    test_app.include_router(create_portfolio_router(price_cache, db_file, 0.0, clock))
    test_app.include_router(create_orders_router(price_cache, db_file))
    test_app.include_router(create_watchlist_router(price_cache, db_file))
    test_app.include_router(create_chat_router(price_cache, db_file, 0.0, clock))
    test_app.include_router(create_market_router(price_cache, clock))

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client, clock, fake_time


def _close_market(clock: SessionClock, fake_time: FakeTime) -> None:
    fake_time.advance(30.0)
    assert clock.tick() == ["close"]


def _reopen_market(clock: SessionClock, fake_time: FakeTime) -> None:
    fake_time.advance(10.0)
    assert clock.tick() == ["open"]


class TestSessionEndpoint:
    """GET /api/market/session contract (fixed — frontend built in parallel)."""

    async def test_open_shape(self, session_env):
        client, _, _ = session_env
        resp = await client.get("/api/market/session")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == SESSION_KEYS
        assert data["state"] == "open"
        assert data["session_id"] == 1
        assert data["next_transition_at"] == data["state_since"] + 30.0
        assert isinstance(data["now"], float)

    async def test_closed_state_reported(self, session_env):
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        data = (await client.get("/api/market/session")).json()
        assert data["state"] == "closed"
        assert data["session_id"] == 1
        assert data["next_transition_at"] == data["state_since"] + 10.0

    async def test_session_id_increments_on_reopen(self, session_env):
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        _reopen_market(clock, fake_time)
        data = (await client.get("/api/market/session")).json()
        assert data["state"] == "open"
        assert data["session_id"] == 2

    async def test_247_mode_has_null_next_transition(self, app_client):
        """Default wiring (no session clock) reports an always-open market."""
        resp = await app_client.get("/api/market/session")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == SESSION_KEYS
        assert data["state"] == "open"
        assert data["session_id"] == 1
        assert data["next_transition_at"] is None


class TestTradingWhileClosed:
    """Equity market orders are rejected while closed; crypto trades 24/7."""

    async def test_equity_market_order_rejected_400(self, session_env):
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Market closed"}
        # Nothing committed.
        portfolio = (await client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == 10000.0
        assert portfolio["positions"] == []

    async def test_crypto_market_order_allowed_while_closed(self, session_env):
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "BTC", "quantity": 0.01, "side": "buy"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_trade_allowed_while_open_and_after_reopen(self, session_env):
        client, clock, fake_time = session_env
        # Open at startup: allowed.
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert resp.status_code == 200
        # Closed: rejected.
        _close_market(clock, fake_time)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert resp.status_code == 400
        # Reopened: allowed again.
        _reopen_market(clock, fake_time)
        resp = await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert resp.status_code == 200

    async def test_resting_orders_may_still_be_placed_while_closed(self, session_env):
        """Non-marketable limit/stop orders rest and evaluate after reopen."""
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        resp = await client.post(
            "/api/portfolio/orders",
            json={
                "ticker": "AAPL",
                "quantity": 1,
                "side": "buy",
                "kind": "limit",
                "limit_price": 100.0,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["order"]["status"] == "open"

    async def test_chat_trade_fails_with_market_closed(self, session_env):
        """Chat trades inherit the rejection via the shared helper (mock buys AAPL)."""
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        resp = await client.post("/api/chat/", json={"message": "buy 5 AAPL"})
        assert resp.status_code == 200  # per-action failures never raise HTTP errors
        trades = resp.json()["trades"]
        assert len(trades) == 1
        assert trades[0]["status"] == "failed"
        assert trades[0]["error"] == "Market closed"

    async def test_chat_trade_succeeds_after_reopen(self, session_env):
        client, clock, fake_time = session_env
        _close_market(clock, fake_time)
        _reopen_market(clock, fake_time)
        resp = await client.post("/api/chat/", json={"message": "buy 5 AAPL"})
        assert resp.status_code == 200
        assert resp.json()["trades"][0]["status"] == "executed"


class TestCryptoWatchlist:
    """Adding BTC via the watchlist works and streams (M3.3)."""

    async def test_add_btc_to_watchlist(self, app_client, fake_market_source):
        resp = await app_client.post("/api/watchlist/", json={"ticker": "BTC"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "ticker": "BTC"}
        assert "BTC" in fake_market_source.get_tickers()

        watchlist = (await app_client.get("/api/watchlist/")).json()["tickers"]
        btc = next(t for t in watchlist if t["ticker"] == "BTC")
        assert btc["price"] == 65000.0  # crypto seed price from the cache
