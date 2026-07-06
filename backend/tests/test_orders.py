"""Tests for the limit-order system.

Covers:
- POST /api/portfolio/orders (open orders, immediate marketable fills, validation)
- GET /api/portfolio/orders (ordering, status filter, limit clamp)
- DELETE /api/portfolio/orders/{order_id} (cancel semantics)
- process_open_orders_once (fill-loop core logic, tested directly)
- Idempotent migration: pre-existing DB files gain the orders table on re-init
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.orders import create_orders_router, process_open_orders_once
from app.routes.portfolio import create_portfolio_router, execute_trade_on_conn

ORDER_JSON_KEYS = {
    "id", "ticker", "side", "quantity", "kind", "limit_price", "stop_price",
    "time_in_force", "expires_at", "triggered_at",
    "status", "reject_reason", "created_at", "filled_at", "fill_price",
}


def _insert_open_order(
    db_file: str, ticker: str, side: str, quantity: float, limit_price: float
) -> str:
    """Insert an open order row directly (for fill-loop unit tests)."""
    order_id = str(uuid.uuid4())
    conn = get_conn(db_file)
    try:
        conn.execute(
            """
            INSERT INTO orders (id, user_id, ticker, side, quantity, limit_price, status, created_at)
            VALUES (?, 'default', ?, ?, ?, ?, 'open', ?)
            """,
            (order_id, ticker, side, quantity, limit_price,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def _get_order_row(db_file: str, order_id: str) -> dict | None:
    conn = get_conn(db_file)
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _db_state(db_file: str) -> SimpleNamespace:
    """Snapshot cash balance, positions, trades, and orders for assertions."""
    conn = get_conn(db_file)
    try:
        cash = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
        trades = [dict(r) for r in conn.execute("SELECT * FROM trades").fetchall()]
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders").fetchall()]
        return SimpleNamespace(cash=cash, positions=positions, trades=trades, orders=orders)
    finally:
        conn.close()


@pytest_asyncio.fixture
async def orders_env(tmp_path, monkeypatch):
    """App client (portfolio + orders routers) plus direct access to cache and DB.

    Mirrors main.py's wiring but exposes the PriceCache so tests can steer
    marketability, and the db path so tests can assert on stored rows.
    """
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_orders_router(price_cache, db_file))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, cache=price_cache, db=db_file)


@pytest.fixture
def fill_env(tmp_path):
    """Initialized DB plus an empty PriceCache for direct fill-loop testing."""
    db_file = str(tmp_path / "fill.db")
    init_db(db_file)
    return SimpleNamespace(cache=PriceCache(), db=db_file)


@pytest.mark.asyncio
class TestPlaceOrder:
    """POST /api/portfolio/orders."""

    async def test_non_marketable_buy_creates_open_order(self, orders_env):
        """A buy limit below the market rests as an open order with the exact JSON shape."""
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 2, "side": "buy", "limit_price": price - 50},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert set(order.keys()) == ORDER_JSON_KEYS
        assert order["ticker"] == "AAPL"
        assert order["side"] == "buy"
        assert order["quantity"] == 2
        assert order["limit_price"] == price - 50
        assert order["status"] == "open"
        assert order["reject_reason"] is None
        assert order["filled_at"] is None
        assert order["fill_price"] is None
        assert order["created_at"]
        # M1 defaults: plain GTC limit order
        assert order["kind"] == "limit"
        assert order["stop_price"] is None
        assert order["time_in_force"] == "gtc"
        assert order["expires_at"] is None
        assert order["triggered_at"] is None

        # No trade executed, cash untouched
        state = _db_state(orders_env.db)
        assert state.cash == 10000.0
        assert state.trades == []
        assert state.positions == []
        assert len(state.orders) == 1
        assert state.orders[0]["fill_trade_id"] is None

    async def test_marketable_buy_fills_immediately_at_ask(self, orders_env):
        """A buy limit at/above the ask executes now: cash, position, trade, filled order."""
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 2, "side": "buy", "limit_price": price + 1},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert set(order.keys()) == ORDER_JSON_KEYS
        assert order["status"] == "filled"
        assert order["fill_price"] == price  # zero spread: ask == price
        assert order["filled_at"] is not None
        assert order["reject_reason"] is None

        state = _db_state(orders_env.db)
        assert state.cash == pytest.approx(10000.0 - 2 * price)
        assert len(state.positions) == 1
        assert state.positions[0]["ticker"] == "AAPL"
        assert state.positions[0]["quantity"] == 2
        assert len(state.trades) == 1
        trade = state.trades[0]
        assert (trade["side"], trade["quantity"], trade["price"]) == ("buy", 2, price)
        # Order row links to the executed trade
        row = _get_order_row(orders_env.db, order["id"])
        assert row["fill_trade_id"] == trade["id"]

    async def test_marketable_sell_fills_immediately_at_bid(self, orders_env):
        """A sell limit at/below the bid executes now (symmetric to buy)."""
        price = orders_env.cache.get_price("AAPL")
        buy = await orders_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 2, "side": "buy"},
        )
        assert buy.status_code == 200

        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "sell", "limit_price": price - 1},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert order["status"] == "filled"
        assert order["fill_price"] == price  # zero spread: bid == price

        state = _db_state(orders_env.db)
        assert state.cash == pytest.approx(10000.0 - 2 * price + price)
        assert state.positions[0]["quantity"] == 1

    async def test_marketable_buy_insufficient_cash_stores_nothing(self, orders_env):
        """Immediate-fill validation failure returns 400 and persists no rows at all."""
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1_000_000, "side": "buy", "limit_price": price + 1},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Insufficient cash"}

        state = _db_state(orders_env.db)
        assert state.cash == 10000.0
        assert state.trades == []
        assert state.positions == []
        assert state.orders == []

    async def test_validation_errors_return_400(self, orders_env):
        """Bad side, non-positive quantity/limit_price, unknown ticker → 400 {"error"}."""
        price = orders_env.cache.get_price("AAPL")
        bad_payloads = [
            {"ticker": "AAPL", "quantity": 1, "side": "hold", "limit_price": price},
            {"ticker": "AAPL", "quantity": 0, "side": "buy", "limit_price": price},
            {"ticker": "AAPL", "quantity": -3, "side": "buy", "limit_price": price},
            {"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": 0},
            {"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": -5},
            {"ticker": "ZZZZ", "quantity": 1, "side": "buy", "limit_price": 100},
        ]
        for payload in bad_payloads:
            resp = await orders_env.client.post("/api/portfolio/orders", json=payload)
            assert resp.status_code == 400, payload
            assert "error" in resp.json()

        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "ZZZZ", "quantity": 1, "side": "buy", "limit_price": 100},
        )
        assert resp.json() == {"error": "Ticker not found in price cache"}

        # Nothing was stored by any of the failed requests
        assert _db_state(orders_env.db).orders == []


@pytest.mark.asyncio
class TestListOrders:
    """GET /api/portfolio/orders."""

    @staticmethod
    async def _place_open_orders(orders_env, count: int = 3) -> list[str]:
        """Place ``count`` non-marketable buy orders; return ids in placement order."""
        price = orders_env.cache.get_price("AAPL")
        ids = []
        for i in range(count):
            resp = await orders_env.client.post(
                "/api/portfolio/orders",
                json={
                    "ticker": "AAPL", "quantity": 1, "side": "buy",
                    "limit_price": price - 50 - i,
                },
            )
            assert resp.status_code == 200
            ids.append(resp.json()["order"]["id"])
        return ids

    async def test_empty_fresh_db(self, orders_env):
        resp = await orders_env.client.get("/api/portfolio/orders")
        assert resp.status_code == 200
        assert resp.json() == {"orders": []}

    async def test_newest_first_default_all(self, orders_env):
        """Default status='all' returns every order, newest first."""
        ids = await self._place_open_orders(orders_env, 3)

        resp = await orders_env.client.get("/api/portfolio/orders")
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        assert [o["id"] for o in orders] == list(reversed(ids))
        for order in orders:
            assert set(order.keys()) == ORDER_JSON_KEYS

    async def test_status_filter_open_only(self, orders_env):
        """?status=open excludes cancelled and filled orders."""
        price = orders_env.cache.get_price("AAPL")
        ids = await self._place_open_orders(orders_env, 2)
        # Cancel one of them
        await orders_env.client.delete(f"/api/portfolio/orders/{ids[0]}")
        # Place a marketable (immediately filled) order
        filled = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": price + 1},
        )
        assert filled.json()["order"]["status"] == "filled"

        resp = await orders_env.client.get("/api/portfolio/orders?status=open")
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        assert [o["id"] for o in orders] == [ids[1]]
        assert all(o["status"] == "open" for o in orders)

        # And the other statuses are reachable through the filter too
        cancelled = await orders_env.client.get("/api/portfolio/orders?status=cancelled")
        assert [o["id"] for o in cancelled.json()["orders"]] == [ids[0]]

    async def test_invalid_status_returns_400(self, orders_env):
        resp = await orders_env.client.get("/api/portfolio/orders?status=bogus")
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_limit_clamped_and_validated(self, orders_env):
        """limit defaults to 50, clamps to 1..500, and 400s on non-integers."""
        await self._place_open_orders(orders_env, 3)

        resp = await orders_env.client.get("/api/portfolio/orders?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["orders"]) == 2

        for clamped_low in ("0", "-5"):
            resp = await orders_env.client.get(f"/api/portfolio/orders?limit={clamped_low}")
            assert resp.status_code == 200
            assert len(resp.json()["orders"]) == 1

        resp = await orders_env.client.get("/api/portfolio/orders?limit=10000")
        assert resp.status_code == 200
        assert len(resp.json()["orders"]) == 3

        for bad in ("abc", "2.5", ""):
            resp = await orders_env.client.get(f"/api/portfolio/orders?limit={bad}")
            assert resp.status_code == 400
            assert "error" in resp.json()


@pytest.mark.asyncio
class TestCancelOrder:
    """DELETE /api/portfolio/orders/{order_id}."""

    async def test_cancel_open_order(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        placed = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": price - 50},
        )
        order_id = placed.json()["order"]["id"]

        resp = await orders_env.client.delete(f"/api/portfolio/orders/{order_id}")
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert set(order.keys()) == ORDER_JSON_KEYS
        assert order["id"] == order_id
        assert order["status"] == "cancelled"
        assert _get_order_row(orders_env.db, order_id)["status"] == "cancelled"

    async def test_cancel_unknown_order_returns_404(self, orders_env):
        resp = await orders_env.client.delete("/api/portfolio/orders/no-such-id")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Order not found"}

    async def test_cancel_filled_order_returns_400(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        placed = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": price + 1},
        )
        order = placed.json()["order"]
        assert order["status"] == "filled"

        resp = await orders_env.client.delete(f"/api/portfolio/orders/{order['id']}")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Order is not open"}


class TestProcessOpenOrdersOnce:
    """Direct unit tests for the fill-loop core (process_open_orders_once)."""

    def test_buy_stays_open_while_ask_above_limit(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_open_order(fill_env.db, "AAPL", "buy", 2, 150.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        assert _get_order_row(fill_env.db, order_id)["status"] == "open"
        state = _db_state(fill_env.db)
        assert state.cash == 10000.0
        assert state.trades == []

    def test_buy_fills_when_ask_drops_to_limit(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_open_order(fill_env.db, "AAPL", "buy", 2, 150.0)
        fill_env.cache.update("AAPL", 150.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}

        row = _get_order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 150.0
        assert row["filled_at"] is not None
        assert row["fill_trade_id"] is not None

        state = _db_state(fill_env.db)
        assert state.cash == pytest.approx(10000.0 - 2 * 150.0)
        assert len(state.positions) == 1
        assert state.positions[0]["ticker"] == "AAPL"
        assert state.positions[0]["quantity"] == 2
        assert state.positions[0]["avg_cost"] == 150.0
        assert len(state.trades) == 1
        assert state.trades[0]["id"] == row["fill_trade_id"]

        # A portfolio snapshot was recorded in the same commit
        conn = get_conn(fill_env.db)
        try:
            snapshots = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
        finally:
            conn.close()
        assert snapshots >= 1

        # Idempotent: a second pass does not double-fill
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 0, "expired": 0}
        assert len(_db_state(fill_env.db).trades) == 1

    def test_buy_marketability_uses_ask_not_last_price(self, fill_env):
        """With a real spread, the ask (not the last price) gates the buy fill."""
        fill_env.cache.update("AAPL", 100.0, bid=99.0, ask=101.0)
        order_id = _insert_open_order(fill_env.db, "AAPL", "buy", 1, 100.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts["skipped"] == 1  # last=100 <= limit but ask=101 > limit

        fill_env.cache.update("AAPL", 100.0, bid=99.0, ask=100.0)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts["filled"] == 1
        row = _get_order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 100.0  # filled at the ask

    def test_sell_fills_when_bid_rises_to_limit(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        # Seed a position: buy 2 @ 200 through the real trade path
        conn = get_conn(fill_env.db)
        try:
            outcome = execute_trade_on_conn(conn, fill_env.cache, "AAPL", "buy", 2)
            assert outcome["status"] == "executed"
            conn.commit()
        finally:
            conn.close()

        order_id = _insert_open_order(fill_env.db, "AAPL", "sell", 2, 250.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        assert _get_order_row(fill_env.db, order_id)["status"] == "open"

        fill_env.cache.update("AAPL", 250.0)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}

        row = _get_order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 250.0
        state = _db_state(fill_env.db)
        assert state.cash == pytest.approx(10000.0 - 2 * 200.0 + 2 * 250.0)
        assert state.positions == []  # fully closed

    def test_insufficient_cash_at_fill_time_rejects(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_open_order(fill_env.db, "AAPL", "buy", 1000, 150.0)
        fill_env.cache.update("AAPL", 150.0)  # marketable, but costs $150k > $10k

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 1, "skipped": 0, "expired": 0}

        row = _get_order_row(fill_env.db, order_id)
        assert row["status"] == "rejected"
        assert row["reject_reason"] == "Insufficient cash"
        assert row["fill_price"] is None
        assert row["fill_trade_id"] is None

        state = _db_state(fill_env.db)
        assert state.cash == 10000.0
        assert state.trades == []
        assert state.positions == []

    def test_insufficient_shares_at_fill_time_rejects(self, fill_env):
        """A resting sell with no position behind it rejects when it becomes marketable."""
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_open_order(fill_env.db, "AAPL", "sell", 5, 150.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 1, "skipped": 0, "expired": 0}
        row = _get_order_row(fill_env.db, order_id)
        assert row["status"] == "rejected"
        assert row["reject_reason"] == "Insufficient shares to sell"

    def test_missing_quote_stays_open(self, fill_env):
        """Orders whose ticker has no quote are left open (ticker may come back)."""
        order_id = _insert_open_order(fill_env.db, "GONE", "buy", 1, 100.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        assert _get_order_row(fill_env.db, order_id)["status"] == "open"

        # Ticker comes back below the limit → fills on a later pass
        fill_env.cache.update("GONE", 90.0)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}
        assert _get_order_row(fill_env.db, order_id)["status"] == "filled"


@pytest.mark.asyncio
class TestOrdersTableMigration:
    """Pre-existing DB files (created before the orders table) upgrade on init."""

    async def test_preexisting_db_without_orders_table_upgrades(self, tmp_path, monkeypatch):
        db_file = str(tmp_path / "old.db")
        monkeypatch.setenv("DB_PATH", db_file)

        # Simulate an old deployment: initialized/seeded DB with NO orders table
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            conn.execute("DROP TABLE orders")
            conn.commit()
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "orders" not in tables
        finally:
            conn.close()

        # App startup runs init_db again — the schema script executes even for
        # existing files, so the orders table is (re)created idempotently
        # without touching existing data.
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "orders" in tables
            cash = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = 'default'"
            ).fetchone()["cash_balance"]
            assert cash == 10000.0  # existing seed data untouched
            watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            assert watchlist_count == 10
        finally:
            conn.close()

        # Orders endpoints are usable against the upgraded database
        price_cache = PriceCache()
        price_cache.update("AAPL", 190.0)
        test_app = FastAPI()
        test_app.include_router(create_orders_router(price_cache, db_file))
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/portfolio/orders",
                json={"ticker": "AAPL", "quantity": 1, "side": "buy", "limit_price": 100.0},
            )
            assert resp.status_code == 200
            assert resp.json()["order"]["status"] == "open"

            listing = await client.get("/api/portfolio/orders")
            assert listing.status_code == 200
            assert len(listing.json()["orders"]) == 1
