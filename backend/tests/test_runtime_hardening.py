"""Focused regression tests for runtime-mode, profile, and quote hardening."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache, SessionClock
from app.market.profiles import CN_PROFILE
from app.routes.auth import create_auth_router
from app.routes.health import router as health_router
from app.routes.orders import _place_order_impl, create_orders_router
from app.routes.portfolio import execute_trade_on_conn, record_snapshots_for_all_users
from app.routes.seasons import create_seasons_router
from app.routes.watchlist import required_market_tickers, ticker_required_by_anyone
from app.settings import CLASSROOM_SERVER, RuntimeSettings
from tests.conftest import FakeMarketSource, FakeTime


def _server_settings() -> RuntimeSettings:
    return RuntimeSettings(
        mode=CLASSROOM_SERVER,
        bind_host="0.0.0.0",
        server_auth_secret="classroom-secret-123",
        admin_token="admin-secret-123456",
        single_replica=True,
    )


class TestRuntimeMode:
    def test_local_demo_cannot_bind_public_interface(self):
        with pytest.raises(ValueError, match="loopback"):
            RuntimeSettings(bind_host="0.0.0.0").validate()

    def test_server_fails_closed_without_required_secrets(self):
        with pytest.raises(ValueError, match="SERVER_AUTH_SECRET"):
            RuntimeSettings(
                mode=CLASSROOM_SERVER,
                bind_host="0.0.0.0",
                admin_token="admin-secret-123456",
                single_replica=True,
            ).validate(db_path="persistent.db")


@pytest.mark.asyncio
async def test_server_login_requires_access_code_and_sets_secure_cookie(tmp_path):
    db_file = str(tmp_path / "server.db")
    init_db(db_file)
    app = FastAPI()
    app.include_router(create_auth_router(db_file, settings=_server_settings()))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (
            await client.post("/api/auth/login", json={"name": "Alice"})
        ).status_code == 401
        assert (
            await client.post(
                "/api/auth/login",
                json={"name": "Alice", "access_code": "wrong"},
            )
        ).status_code == 403
        response = await client.post(
            "/api/auth/login",
            json={"name": "Alice", "access_code": "classroom-secret-123"},
        )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_cn_new_login_uses_cn_cash_and_watchlist(tmp_path):
    db_file = str(tmp_path / "cn-login.db")
    init_db(
        db_file,
        seed_cash=CN_PROFILE.seed_cash,
        default_watchlist=list(CN_PROFILE.universe.default_watchlist),
    )
    app = FastAPI()
    app.include_router(create_auth_router(db_file, profile=CN_PROFILE))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/auth/login", json={"name": "Alice"})
    assert response.status_code == 200
    conn = get_conn(db_file)
    try:
        cash = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'alice'"
        ).fetchone()[0]
        tickers = {
            row["ticker"]
            for row in conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = 'alice'"
            )
        }
    finally:
        conn.close()
    assert cash == CN_PROFILE.seed_cash
    assert tickers == set(CN_PROFILE.universe.default_watchlist)


@pytest.mark.asyncio
async def test_readiness_reports_stale_then_recovers():
    cache = PriceCache(max_quote_age_seconds=5)
    source = FakeMarketSource(cache)
    await source.start(["AAPL"])
    cache.update("AAPL", 100, timestamp=time.time() - 10)
    app = FastAPI()
    app.state.price_cache = cache
    app.state.market_source = source
    app.include_router(health_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        stale = await client.get("/api/ready")
        cache.update("AAPL", 101)
        ready = await client.get("/api/ready")
    assert stale.status_code == 503
    assert stale.json()["stale"] == ["AAPL"]
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_stale_quote_cannot_execute_trade(tmp_path):
    db_file = str(tmp_path / "stale.db")
    init_db(db_file)
    cache = PriceCache(max_quote_age_seconds=1)
    cache.update("AAPL", 100, timestamp=time.time() - 10)
    conn = get_conn(db_file)
    try:
        result = execute_trade_on_conn(conn, cache, "AAPL", "buy", 1)
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()
    assert result == {"status": "failed", "ticker": "AAPL", "error": "Quote is stale"}
    assert trade_count == 0


def test_position_keeps_ticker_in_market_demand_and_values_at_cost(tmp_path):
    db_file = str(tmp_path / "market-demand.db")
    init_db(db_file)
    conn = get_conn(db_file)
    try:
        conn.execute("DELETE FROM watchlist WHERE ticker = 'AAPL'")
        conn.execute(
            "INSERT INTO positions "
            "(id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES ('offline-position', 'default', 'AAPL', 2, 123, 'now')"
        )
        conn.commit()
        assert ticker_required_by_anyone(conn, "AAPL") is True
        assert "AAPL" in required_market_tickers(conn)
        assert record_snapshots_for_all_users(conn, PriceCache()) == 1
        snapshot = conn.execute(
            "SELECT total_value FROM portfolio_snapshots ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert snapshot["total_value"] == 10_246.0


@pytest.mark.asyncio
async def test_non_finite_trade_quantity_is_rejected(app_client):
    response = await app_client.post(
        "/api/portfolio/trade",
        json={"ticker": "AAPL", "side": "buy", "quantity": "NaN"},
    )
    assert response.status_code == 422


def _closed_clock() -> SessionClock:
    """A cycling session clock deterministically advanced into CLOSED."""
    fake_time = FakeTime()
    clock = SessionClock(30.0, 10.0, now=fake_time)
    fake_time.advance(31.0)
    assert clock.tick() == ["close"]
    assert not clock.is_open
    return clock


class TestAfterHoursOrderPlacement:
    """Quote freshness gates placement only while the ticker's market is open."""

    def _stale_cache(self, ticker: str = "AAPL", price: float = 100.0) -> PriceCache:
        cache = PriceCache(max_quote_age_seconds=1)
        cache.update(ticker, price, timestamp=time.time() - 10)
        return cache

    def test_closed_market_accepts_resting_gtc_limit(self, tmp_path):
        db_file = str(tmp_path / "afterhours.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            result = _place_order_impl(
                conn, self._stale_cache(),
                ticker="AAPL", side="buy", quantity=1, kind="limit",
                limit_price=90.0, stop_price=None, time_in_force="gtc",
                session_clock=_closed_clock(),
            )
            conn.commit()
        finally:
            conn.close()
        assert result["status"] == "open"

    def test_closed_market_marketable_limit_rests_instead_of_stale_fill(self, tmp_path):
        # A limit through the frozen price must NOT fill at the stale quote —
        # it rests and the fill loop executes it once fresh quotes return.
        db_file = str(tmp_path / "afterhours-marketable.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            result = _place_order_impl(
                conn, self._stale_cache(),
                ticker="AAPL", side="buy", quantity=1, kind="limit",
                limit_price=250.0, stop_price=None, time_in_force="gtc",
                session_clock=_closed_clock(),
            )
            conn.commit()
        finally:
            conn.close()
        assert result["status"] == "open"
        assert result["fill_price"] is None

    def test_open_market_stale_quote_still_rejects_placement(self, tmp_path):
        db_file = str(tmp_path / "stale-open.db")
        init_db(db_file)
        fake_time = FakeTime()
        open_clock = SessionClock(30.0, 10.0, now=fake_time)
        assert open_clock.is_open
        conn = get_conn(db_file)
        try:
            result = _place_order_impl(
                conn, self._stale_cache(),
                ticker="AAPL", side="buy", quantity=1, kind="limit",
                limit_price=90.0, stop_price=None, time_in_force="gtc",
                session_clock=open_clock,
            )
        finally:
            conn.close()
        assert result == {"status": "failed", "ticker": "AAPL", "error": "Quote is stale"}

    def test_closed_market_stale_crypto_quote_still_rejected(self, tmp_path):
        # Crypto trades 24/7 — an equity session close never excuses a stale
        # crypto quote.
        db_file = str(tmp_path / "stale-crypto.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            result = _place_order_impl(
                conn, self._stale_cache("BTC", 65000.0),
                ticker="BTC", side="buy", quantity=0.1, kind="limit",
                limit_price=60000.0, stop_price=None, time_in_force="gtc",
                session_clock=_closed_clock(),
            )
        finally:
            conn.close()
        assert result == {"status": "failed", "ticker": "BTC", "error": "Quote is stale"}

    @pytest.mark.asyncio
    async def test_route_places_gtc_limit_while_closed(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "afterhours-route.db")
        monkeypatch.setenv("DB_PATH", db_file)
        init_db(db_file)
        app = FastAPI()
        app.include_router(
            create_orders_router(
                self._stale_cache(), db_file, session_clock=_closed_clock()
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/portfolio/orders",
                json={"ticker": "AAPL", "side": "buy", "quantity": 1,
                      "kind": "limit", "limit_price": 90.0},
            )
        assert resp.status_code == 200
        assert resp.json()["order"]["status"] == "open"


def test_stale_rejection_warning_is_throttled_per_ticker(caplog):
    cache = PriceCache(max_quote_age_seconds=1)
    cache.update("AAPL", 100, timestamp=time.time() - 10)
    cache.update("MSFT", 100, timestamp=time.time() - 10)
    with caplog.at_level(logging.WARNING, logger="app.market.cache"):
        for _ in range(3):
            cache.warn_stale_rejection("AAPL")
        cache.warn_stale_rejection("MSFT")
    messages = [record.getMessage() for record in caplog.records]
    aapl = [m for m in messages if "AAPL" in m]
    msft = [m for m in messages if "MSFT" in m]
    assert len(aapl) == 1  # throttled: one warning per ticker per 60s window
    assert len(msft) == 1  # ...but each ticker gets its own line
    assert "quote age" in aapl[0]


def test_stale_trade_rejection_emits_observability_warning(tmp_path, caplog):
    # The 45s fail-closed freeze must not be silent: the rejection path logs
    # a throttled warning naming the ticker and the quote age.
    db_file = str(tmp_path / "stale-warn.db")
    init_db(db_file)
    cache = PriceCache(max_quote_age_seconds=1)
    cache.update("AAPL", 100, timestamp=time.time() - 10)
    conn = get_conn(db_file)
    try:
        with caplog.at_level(logging.WARNING, logger="app.market.cache"):
            result = execute_trade_on_conn(conn, cache, "AAPL", "buy", 1)
    finally:
        conn.close()
    assert result["error"] == "Quote is stale"
    assert any(
        "AAPL" in record.getMessage() and "stale quote" in record.getMessage()
        for record in caplog.records
    )


class TestSeasonAdminByMode:
    """Season reset admin-token enforcement is classroom-server only (the
    Idempotency-Key stays mandatory in every mode)."""

    def _reset_app(self, db_file: str, settings: RuntimeSettings | None) -> FastAPI:
        app = FastAPI()
        app.include_router(
            create_seasons_router(PriceCache(), db_file, settings=settings)
        )
        return app

    @pytest.mark.asyncio
    async def test_server_mode_requires_admin_token(self, tmp_path):
        db_file = str(tmp_path / "server-seasons.db")
        init_db(db_file)
        app = self._reset_app(db_file, _server_settings())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            missing = await client.post(
                "/api/season/reset",
                json={"confirm": True},
                headers={"idempotency-key": "no-token"},
            )
            assert missing.status_code == 401
            assert missing.json() == {"error": "Administrator token required"}
            ok = await client.post(
                "/api/season/reset",
                json={"confirm": True},
                headers={
                    "x-finally-admin-token": "admin-secret-123456",
                    "idempotency-key": "with-token",
                },
            )
        assert ok.status_code == 200
        assert ok.json()["season"]["id"] == 2

    @pytest.mark.asyncio
    async def test_local_demo_reset_needs_no_token(self, tmp_path):
        db_file = str(tmp_path / "local-seasons.db")
        init_db(db_file)
        app = self._reset_app(db_file, None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/season/reset",
                json={"confirm": True},
                headers={"idempotency-key": "local-no-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["season"]["id"] == 2


def test_init_db_heals_legacy_duplicate_open_seasons(tmp_path):
    # Volumes created before idx_seasons_one_current may hold several rows
    # with ended_at IS NULL; creating the unique partial index over them
    # would raise IntegrityError and brick startup. init_db must heal first:
    # keep the newest started_at as current, stamp the rest ended_at.
    import sqlite3

    db_file = str(tmp_path / "legacy-seasons.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE seasons ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "started_at TEXT NOT NULL, ended_at TEXT)"
    )
    conn.execute("INSERT INTO seasons (started_at) VALUES ('2026-01-01T00:00:00')")
    conn.execute("INSERT INTO seasons (started_at) VALUES ('2026-03-01T00:00:00')")
    conn.execute("INSERT INTO seasons (started_at) VALUES ('2026-02-01T00:00:00')")
    conn.commit()
    conn.close()

    init_db(db_file)  # must not raise

    conn = get_conn(db_file)
    try:
        open_rows = conn.execute(
            "SELECT id FROM seasons WHERE ended_at IS NULL"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
    finally:
        conn.close()
    assert [row["id"] for row in open_rows] == [2]  # latest started_at stays open
    assert total == 3

    init_db(db_file)  # idempotent — healed volumes start cleanly forever after
