"""Tests for the place_order_on_conn helper (M2.1).

Verifies the shared placement path used by both POST /api/portfolio/orders
and the chat auto-execution pipeline:
- All validation failures return {"status": "failed", ...} dicts (no raise)
- Resting placements return the full order JSON with status 'open'
- Immediately-marketable limit orders return status 'filled' with the trade
  and snapshot staged on the caller's (uncommitted) transaction
- The helper never commits — the caller owns the transaction boundary
- Failures unwind only the current order's writes (SAVEPOINT), leaving
  sibling writes on the caller's open transaction intact
- The HTTP route still maps helper failures to 400 with nothing stored
"""

from __future__ import annotations

import inspect

import pytest

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.orders import place_order_on_conn
from app.routes.portfolio import execute_trade_on_conn
from tests.test_orders import ORDER_JSON_KEYS

AAPL_PRICE = SEED_PRICES["AAPL"]  # 190.0


@pytest.fixture
def order_db(tmp_path):
    """Fresh initialized SQLite connection + its path for direct helper testing."""
    db_file = str(tmp_path / "test_place_order.db")
    init_db(db_file)
    conn = get_conn(db_file)
    yield conn, db_file
    conn.close()


@pytest.fixture
def seeded_cache():
    """Price cache populated with seed prices (zero spread)."""
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)
    return cache


def _place(conn, cache, **overrides) -> dict:
    """Call place_order_on_conn with sensible defaults (resting AAPL limit buy)."""
    kwargs: dict = dict(
        ticker="AAPL",
        side="buy",
        quantity=1.0,
        kind="limit",
        limit_price=100.0,  # well below AAPL's 190 — rests as 'open'
        stop_price=None,
        time_in_force="gtc",
        commission_bps=0.0,
    )
    kwargs.update(overrides)
    return place_order_on_conn(conn, cache, **kwargs)


class TestPlaceOrderOnConnSignature:
    def test_importable(self):
        assert callable(place_order_on_conn)

    def test_signature_keyword_only(self):
        sig = inspect.signature(place_order_on_conn)
        params = list(sig.parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "kind",
            "limit_price", "stop_price", "time_in_force", "commission_bps",
            "user_id",
        ]
        for name in params[2:]:
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


class TestPlaceOrderOnConnFailurePaths:
    """All validation failures return {"status": "failed", ...} — never raise."""

    @pytest.mark.parametrize(
        ("overrides", "expected_error"),
        [
            pytest.param({"side": "hold"}, "Side must be", id="bad-side"),
            pytest.param({"quantity": 0}, "Quantity must be greater than 0", id="zero-qty"),
            pytest.param({"quantity": -3}, "Quantity must be greater than 0", id="negative-qty"),
            pytest.param({"kind": "market"}, "kind must be one of", id="bad-kind"),
            pytest.param({"time_in_force": "fok"}, "time_in_force must be", id="bad-tif"),
            pytest.param({"limit_price": None}, "limit_price is required", id="limit-missing-price"),
            pytest.param(
                {"kind": "stop", "limit_price": None, "stop_price": None},
                "stop_price is required", id="stop-missing-price",
            ),
            pytest.param(
                {"kind": "stop", "limit_price": None, "stop_price": 250.0, "side": "sell"},
                "Stop price must be below the market", id="wrong-side-sell-stop",
            ),
            pytest.param(
                {"kind": "stop", "limit_price": None, "stop_price": 100.0, "side": "buy"},
                "Stop price must be above the market", id="wrong-side-buy-stop",
            ),
            pytest.param({"limit_price": -5.0}, "Limit price must be greater than 0", id="negative-limit"),
        ],
    )
    def test_validation_failure_returns_failed_dict(
        self, order_db, seeded_cache, overrides, expected_error
    ):
        conn, _ = order_db
        result = _place(conn, seeded_cache, **overrides)
        assert result["status"] == "failed"
        assert expected_error in result["error"]
        assert result["ticker"] == "AAPL"
        assert set(result.keys()) == {"status", "ticker", "error"}

    def test_unknown_ticker_returns_failed_dict(self, order_db):
        conn, _ = order_db
        result = _place(conn, PriceCache())
        assert result == {
            "status": "failed",
            "ticker": "AAPL",
            "error": "Ticker not found in price cache",
        }

    def test_insufficient_cash_on_marketable_fill_returns_failed_dict(
        self, order_db, seeded_cache
    ):
        """A marketable limit buy whose fill fails validation stores NOTHING."""
        conn, db_file = order_db
        # 100 shares * $190 = $19,000 > $10,000 cash; limit 250 is marketable.
        result = _place(conn, seeded_cache, quantity=100.0, limit_price=250.0)
        assert result["status"] == "failed"
        assert result["error"] == "Insufficient cash"
        conn.rollback()  # release the write lock the helper took

        check = get_conn(db_file)
        try:
            assert check.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
            assert check.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
            cash = check.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()["cash_balance"]
            assert cash == 10000.0
        finally:
            check.close()


class TestPlaceOrderOnConnSuccessPaths:
    def test_resting_limit_returns_open_order_json_and_does_not_commit(
        self, order_db, seeded_cache
    ):
        conn, db_file = order_db
        result = _place(conn, seeded_cache)
        # Public order JSON shape, status 'open'
        assert set(result.keys()) == ORDER_JSON_KEYS
        assert result["status"] == "open"
        assert result["ticker"] == "AAPL"
        assert result["kind"] == "limit"
        assert result["limit_price"] == 100.0
        assert result["time_in_force"] == "gtc"
        assert result["expires_at"] is None
        assert result["filled_at"] is None and result["fill_price"] is None

        # NOT committed yet: a second connection must not see the row
        check = get_conn(db_file)
        try:
            assert check.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        finally:
            check.close()

        conn.commit()
        check = get_conn(db_file)
        try:
            row = check.execute("SELECT status FROM orders WHERE id = ?", (result["id"],)).fetchone()
            assert row is not None and row["status"] == "open"
        finally:
            check.close()

    def test_marketable_limit_fills_with_trade_and_snapshot(self, order_db, seeded_cache):
        conn, db_file = order_db
        result = _place(conn, seeded_cache, limit_price=250.0)
        assert result["status"] == "filled"
        assert result["fill_price"] == AAPL_PRICE
        assert result["filled_at"] is not None
        conn.commit()

        check = get_conn(db_file)
        try:
            trade = check.execute("SELECT ticker, side, price FROM trades").fetchone()
            assert trade["ticker"] == "AAPL" and trade["side"] == "buy"
            assert trade["price"] == AAPL_PRICE
            assert check.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1
            cash = check.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()["cash_balance"]
            assert cash == pytest.approx(10000.0 - AAPL_PRICE)
        finally:
            check.close()

    def test_normalization_and_none_tif_defaults_to_gtc(self, order_db, seeded_cache):
        conn, _ = order_db
        result = _place(
            conn, seeded_cache, ticker="  aapl ", side="BUY", kind="LIMIT",
            time_in_force=None,
        )
        assert result["status"] == "open"
        assert result["ticker"] == "AAPL"
        assert result["side"] == "buy"
        assert result["kind"] == "limit"
        assert result["time_in_force"] == "gtc"

    def test_day_tif_stamps_expires_at(self, order_db, seeded_cache):
        conn, _ = order_db
        result = _place(conn, seeded_cache, time_in_force="day")
        assert result["status"] == "open"
        assert result["expires_at"] is not None

    def test_failed_order_preserves_sibling_writes_in_open_transaction(
        self, order_db, seeded_cache
    ):
        """SAVEPOINT semantics: a failed placement must not unwind the caller's
        earlier writes (the chat batch relies on this)."""
        conn, db_file = order_db
        trade = execute_trade_on_conn(conn, seeded_cache, "AAPL", "buy", 1.0)
        assert trade["status"] == "executed"

        # Marketable buy that fails at fill time (insufficient cash for 100 sh)
        failed = _place(conn, seeded_cache, quantity=100.0, limit_price=250.0)
        assert failed["status"] == "failed"

        # And a sibling that succeeds afterwards on the same transaction
        resting = _place(conn, seeded_cache)
        assert resting["status"] == "open"

        conn.commit()
        check = get_conn(db_file)
        try:
            assert check.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
            assert check.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
            cash = check.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()["cash_balance"]
            assert cash == pytest.approx(10000.0 - AAPL_PRICE)
        finally:
            check.close()


@pytest.mark.asyncio
class TestPlaceOrderRouteStill400s:
    """The HTTP route keeps its behavior: helper failures map to HTTP 400."""

    async def test_insufficient_cash_marketable_returns_400_stores_nothing(self, app_client):
        resp = await app_client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 100, "side": "buy",
                  "kind": "limit", "limit_price": 250.0},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Insufficient cash"}

        orders = (await app_client.get("/api/portfolio/orders")).json()["orders"]
        assert orders == []
        portfolio = (await app_client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == 10000.0

    async def test_validation_failure_returns_400(self, app_client):
        resp = await app_client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy", "kind": "bogus"},
        )
        assert resp.status_code == 400
        assert "kind must be one of" in resp.json()["error"]

    async def test_route_success_shape_unchanged(self, app_client):
        resp = await app_client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 2, "side": "buy",
                  "kind": "limit", "limit_price": 100.0},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert set(order.keys()) == ORDER_JSON_KEYS
        assert order["status"] == "open"
