"""Tests for M1 commission and realized P&L (Task C).

Commission = notional * FINALLY_COMMISSION_BPS / 10000, rounded to 2dp,
applied to ALL fills (market trades, chat trades, order fills):
- buys pay notional + commission and fold the commission into cost basis
- sells receive notional - commission and record
  realized_pnl = round((fill_price - avg_cost_at_sale) * qty - commission, 2)
- trade rows always store commission; buys store realized_pnl NULL
- GET /api/portfolio exposes lifetime realized_pnl (sum over trades)

With the env unset (bps = 0) numeric behavior must be identical to pre-M1 —
the whole legacy suite runs at 0 bps and enforces that.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.main import _read_commission_bps
from app.market import PriceCache
from app.routes.orders import create_orders_router, process_open_orders_once
from app.routes.portfolio import create_portfolio_router, execute_trade_on_conn

BPS = 10.0  # 10 bps = 0.1% commission used throughout these tests


@pytest.fixture
def fresh_db(tmp_path):
    """Fresh initialized SQLite connection for direct helper testing."""
    db_file = str(tmp_path / "commission.db")
    init_db(db_file)
    conn = get_conn(db_file)
    yield conn
    conn.close()


@pytest.fixture
def cache():
    c = PriceCache()
    c.update("AAPL", 100.0)
    return c


def _cash(conn) -> float:
    return conn.execute(
        "SELECT cash_balance FROM users_profile WHERE id = 'default'"
    ).fetchone()["cash_balance"]


def _avg_cost(conn, ticker: str) -> float | None:
    row = conn.execute(
        "SELECT avg_cost FROM positions WHERE user_id = 'default' AND ticker = ?",
        (ticker,),
    ).fetchone()
    return row["avg_cost"] if row else None


def _trade_rows(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute("SELECT * FROM trades ORDER BY rowid ASC").fetchall()
    ]


class TestReadCommissionBps:
    """FINALLY_COMMISSION_BPS is parsed once at startup by main._read_commission_bps."""

    def test_unset_defaults_to_zero(self, monkeypatch):
        monkeypatch.delenv("FINALLY_COMMISSION_BPS", raising=False)
        assert _read_commission_bps() == 0.0

    def test_empty_defaults_to_zero(self, monkeypatch):
        monkeypatch.setenv("FINALLY_COMMISSION_BPS", "  ")
        assert _read_commission_bps() == 0.0

    def test_parses_float(self, monkeypatch):
        monkeypatch.setenv("FINALLY_COMMISSION_BPS", "10")
        assert _read_commission_bps() == 10.0
        monkeypatch.setenv("FINALLY_COMMISSION_BPS", "2.5")
        assert _read_commission_bps() == 2.5

    def test_invalid_falls_back_to_zero(self, monkeypatch):
        monkeypatch.setenv("FINALLY_COMMISSION_BPS", "free")
        assert _read_commission_bps() == 0.0

    def test_negative_falls_back_to_zero(self, monkeypatch):
        monkeypatch.setenv("FINALLY_COMMISSION_BPS", "-5")
        assert _read_commission_bps() == 0.0


class TestCommissionOnTrades:
    """Direct execute_trade_on_conn math at 10 bps."""

    def test_buy_pays_commission_and_folds_into_cost_basis(self, fresh_db, cache):
        outcome = execute_trade_on_conn(
            fresh_db, cache, "AAPL", "buy", 10, commission_bps=BPS
        )
        assert outcome["status"] == "executed"
        # notional = 10 * 100 = 1000; commission = 1000 * 10/10000 = 1.00
        assert outcome["commission"] == 1.0
        assert outcome["realized_pnl"] is None
        assert outcome["price"] == 100.0

        # Cash decreases by notional + commission
        assert _cash(fresh_db) == pytest.approx(10000.0 - 1000.0 - 1.0)
        # Commission folds into cost basis: (1000 + 1) / 10 = 100.10
        assert _avg_cost(fresh_db, "AAPL") == pytest.approx(100.1)

        trades = _trade_rows(fresh_db)
        assert len(trades) == 1
        assert trades[0]["commission"] == 1.0
        assert trades[0]["realized_pnl"] is None

    def test_buy_cost_basis_weighted_across_lots(self, fresh_db, cache):
        execute_trade_on_conn(fresh_db, cache, "AAPL", "buy", 10, commission_bps=BPS)
        cache.update("AAPL", 200.0)
        execute_trade_on_conn(fresh_db, cache, "AAPL", "buy", 10, commission_bps=BPS)
        # Lot 1: (1000 + 1.0)/10 = 100.10; lot 2: (2000 + 2.0)/10 = 200.20
        # Weighted: (100.10*10 + 200.20*10) / 20 = 150.15
        assert _avg_cost(fresh_db, "AAPL") == pytest.approx(150.15)

    def test_sell_receives_proceeds_minus_commission_and_realizes_pnl(
        self, fresh_db, cache
    ):
        execute_trade_on_conn(fresh_db, cache, "AAPL", "buy", 10, commission_bps=BPS)
        cache.update("AAPL", 110.0)
        outcome = execute_trade_on_conn(
            fresh_db, cache, "AAPL", "sell", 10, commission_bps=BPS
        )
        assert outcome["status"] == "executed"
        # notional = 1100; commission = 1.10
        assert outcome["commission"] == 1.1
        # realized = (110 - 100.10) * 10 - 1.10 = 99.00 - 1.10 = 97.90
        assert outcome["realized_pnl"] == pytest.approx(97.9)

        # Cash: 10000 - 1001 (buy) + 1100 - 1.10 (sell) = 10097.90
        assert _cash(fresh_db) == pytest.approx(10097.9)
        sell_row = _trade_rows(fresh_db)[-1]
        assert sell_row["commission"] == 1.1
        assert sell_row["realized_pnl"] == pytest.approx(97.9)

    def test_insufficient_cash_boundary_includes_commission(self, fresh_db, cache):
        """A buy affordable on notional alone must fail once commission is added."""
        # 100 shares * $100 = exactly the $10,000 balance; commission ($10) tips it over
        outcome = execute_trade_on_conn(
            fresh_db, cache, "AAPL", "buy", 100, commission_bps=BPS
        )
        assert outcome["status"] == "failed"
        assert outcome["error"] == "Insufficient cash"
        assert _cash(fresh_db) == 10000.0
        assert _trade_rows(fresh_db) == []

        # Slightly smaller order fits including commission: 99*100 + 9.90 = 9909.90
        outcome = execute_trade_on_conn(
            fresh_db, cache, "AAPL", "buy", 99, commission_bps=BPS
        )
        assert outcome["status"] == "executed"
        assert _cash(fresh_db) == pytest.approx(10000.0 - 9900.0 - 9.9)

    def test_zero_bps_matches_legacy_math_exactly(self, fresh_db, cache):
        """With commission_bps=0 (env unset) the math is identical to pre-M1."""
        outcome = execute_trade_on_conn(fresh_db, cache, "AAPL", "buy", 10)
        assert outcome["commission"] == 0.0
        assert _cash(fresh_db) == 10000.0 - 10 * 100.0  # exact, not approx
        assert _avg_cost(fresh_db, "AAPL") == 100.0  # exact seed price, no fold


@pytest_asyncio.fixture
async def commissioned_env(tmp_path, monkeypatch):
    """App client with portfolio + orders routers built at 10 bps commission."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    price_cache.update("AAPL", 100.0)

    test_app = FastAPI()
    test_app.include_router(create_portfolio_router(price_cache, db_file, BPS))
    test_app.include_router(create_orders_router(price_cache, db_file, BPS))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, cache=price_cache, db=db_file)


@pytest.mark.asyncio
class TestCommissionViaRoutes:
    """Commission applies to ALL fill paths and shows up in the trade JSON."""

    async def test_trade_response_includes_commission_and_realized_pnl(
        self, commissioned_env
    ):
        buy = await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 10, "side": "buy"},
        )
        assert buy.status_code == 200
        data = buy.json()
        assert data["commission"] == 1.0
        assert data["realized_pnl"] is None

        commissioned_env.cache.update("AAPL", 110.0)
        sell = await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 10, "side": "sell"},
        )
        assert sell.status_code == 200
        data = sell.json()
        assert data["commission"] == 1.1
        assert data["realized_pnl"] == pytest.approx(97.9)

        # Blotter items carry both keys with the stored values
        trades = (await commissioned_env.client.get("/api/portfolio/trades")).json()["trades"]
        assert trades[0]["side"] == "sell"
        assert trades[0]["commission"] == 1.1
        assert trades[0]["realized_pnl"] == pytest.approx(97.9)
        assert trades[1]["side"] == "buy"
        assert trades[1]["commission"] == 1.0
        assert trades[1]["realized_pnl"] is None

    async def test_portfolio_reports_lifetime_realized_pnl(self, commissioned_env):
        await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 10, "side": "buy"},
        )
        commissioned_env.cache.update("AAPL", 110.0)
        await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 5, "side": "sell"},
        )
        commissioned_env.cache.update("AAPL", 120.0)
        await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 5, "side": "sell"},
        )

        # sell 1: (110-100.10)*5 - round(550*0.001,2)=0.55 → 48.95
        # sell 2: (120-100.10)*5 - round(600*0.001,2)=0.60 → 98.90
        portfolio = (await commissioned_env.client.get("/api/portfolio/")).json()
        assert portfolio["realized_pnl"] == pytest.approx(48.95 + 98.90)

    async def test_insufficient_cash_via_route_includes_commission(
        self, commissioned_env
    ):
        resp = await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 100, "side": "buy"},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Insufficient cash"}

    async def test_immediate_limit_fill_pays_commission(self, commissioned_env):
        """The marketable-at-placement path charges the same commission."""
        resp = await commissioned_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 10, "side": "buy", "limit_price": 101.0},
        )
        assert resp.status_code == 200
        assert resp.json()["order"]["status"] == "filled"

        portfolio = (await commissioned_env.client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == pytest.approx(10000.0 - 1000.0 - 1.0)
        trades = (await commissioned_env.client.get("/api/portfolio/trades")).json()["trades"]
        assert trades[0]["commission"] == 1.0

    async def test_fill_loop_fill_pays_commission(self, commissioned_env):
        """Resting orders filled by the loop charge commission and store realized P&L."""
        # Buy 10 through the commissioned trade path (avg cost 100.10)
        await commissioned_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 10, "side": "buy"},
        )
        # Rest a sell limit above the market, then let the market rise to it
        resp = await commissioned_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 10, "side": "sell", "limit_price": 110.0},
        )
        assert resp.json()["order"]["status"] == "open"

        commissioned_env.cache.update("AAPL", 110.0)
        counts = process_open_orders_once(
            commissioned_env.db, commissioned_env.cache, commission_bps=BPS
        )
        assert counts["filled"] == 1

        trades = (await commissioned_env.client.get("/api/portfolio/trades")).json()["trades"]
        assert trades[0]["side"] == "sell"
        assert trades[0]["commission"] == 1.1
        assert trades[0]["realized_pnl"] == pytest.approx(97.9)
        portfolio = (await commissioned_env.client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == pytest.approx(10000.0 - 1001.0 + 1100.0 - 1.1)
        assert portfolio["realized_pnl"] == pytest.approx(97.9)


@pytest.mark.asyncio
class TestChatTradesPayCommission:
    """Chat-executed trades run through the same commissioned path."""

    async def test_mock_chat_buy_charges_commission(self, tmp_path, monkeypatch):
        from app.routes.chat import create_chat_router

        db_file = str(tmp_path / "chat.db")
        monkeypatch.setenv("DB_PATH", db_file)
        monkeypatch.setenv("LLM_MOCK", "true")
        init_db(db_file)

        price_cache = PriceCache()
        price_cache.update("AAPL", 100.0)
        price_cache.update("PYPL", 60.0)  # mock turn also adds PYPL to watchlist

        test_app = FastAPI()
        test_app.state.market_source = None
        test_app.include_router(create_portfolio_router(price_cache, db_file, BPS))
        test_app.include_router(create_chat_router(price_cache, db_file, BPS))

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/chat/", json={"message": "Buy AAPL"})
            assert resp.status_code == 200
            trade = resp.json()["trades"][0]  # mock buys 5 AAPL
            assert trade["status"] == "executed"
            # notional = 5 * 100 = 500; commission = 0.50
            assert trade["commission"] == 0.5
            assert trade["realized_pnl"] is None

            portfolio = (await client.get("/api/portfolio/")).json()
            assert portfolio["cash"] == pytest.approx(10000.0 - 500.0 - 0.5)


@pytest.mark.asyncio
class TestRealizedPnlWithoutCommission:
    """Realized P&L is tracked even at 0 bps (the default deployment)."""

    async def test_round_trip_realizes_exact_pnl(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "test.db")
        monkeypatch.setenv("DB_PATH", db_file)
        init_db(db_file)

        price_cache = PriceCache()
        price_cache.update("AAPL", 100.0)
        test_app = FastAPI()
        test_app.include_router(create_portfolio_router(price_cache, db_file))

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            # Fresh DB reports 0.0 (not null) before any sells
            portfolio = (await client.get("/api/portfolio/")).json()
            assert portfolio["realized_pnl"] == 0.0

            buy = await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 10, "side": "buy"},
            )
            assert buy.status_code == 200
            assert buy.json()["commission"] == 0.0
            assert buy.json()["realized_pnl"] is None

            price_cache.update("AAPL", 125.5)
            sell = await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 10, "side": "sell"},
            )
            assert sell.status_code == 200
            # (125.50 - 100.00) * 10 - 0 = 255.00 exactly
            assert sell.json()["realized_pnl"] == 255.0
            assert sell.json()["commission"] == 0.0

            # Stored on the trade row...
            trades = (await client.get("/api/portfolio/trades")).json()["trades"]
            assert trades[0]["side"] == "sell"
            assert trades[0]["realized_pnl"] == 255.0
            assert trades[1]["side"] == "buy"
            assert trades[1]["realized_pnl"] is None

            # ...and aggregated in the portfolio summary
            portfolio = (await client.get("/api/portfolio/")).json()
            assert portfolio["realized_pnl"] == 255.0

    async def test_loss_is_negative_realized_pnl(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "test.db")
        monkeypatch.setenv("DB_PATH", db_file)
        init_db(db_file)

        price_cache = PriceCache()
        price_cache.update("AAPL", 200.0)
        test_app = FastAPI()
        test_app.include_router(create_portfolio_router(price_cache, db_file))

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 10, "side": "buy"},
            )
            price_cache.update("AAPL", 150.0)
            sell = await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 10, "side": "sell"},
            )
            assert sell.json()["realized_pnl"] == -500.0
            portfolio = (await client.get("/api/portfolio/")).json()
            assert portfolio["realized_pnl"] == -500.0
