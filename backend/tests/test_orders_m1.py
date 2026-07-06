"""Tests for M1 advanced orders: schema migration, stop / stop_limit, time-in-force.

Covers (PLATFORM_ROADMAP §M1):
- Idempotent COLUMN migration: pre-existing DBs created before the M1 columns
  (orders.kind/stop_price/time_in_force/expires_at/triggered_at,
  trades.commission/realized_pnl) upgrade on init_db and the endpoints work
- Stop order placement validation matrix (wrong-side rejects, required-fields
  matrix, enum validation, price positivity)
- Stop trigger semantics in the fill loop: SELL stop on bid <= stop fills at
  bid; BUY stop on ask >= stop fills at ask; untriggered stays open
- Stop-limit: trigger stamps triggered_at, then normal limit semantics
- Time-in-force: day orders expire via the fill loop; gtc untouched; cancel
  on an expired order returns 400
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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
from tests.test_orders import ORDER_JSON_KEYS

# The orders/trades DDL as it shipped BEFORE M1 — used to simulate an existing
# database volume that must upgrade in place on startup.
_PRE_M1_TABLES = """
CREATE TABLE orders (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    ticker        TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      REAL NOT NULL,
    limit_price   REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    filled_at     TEXT,
    fill_price    REAL,
    fill_trade_id TEXT
);
CREATE TABLE trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
);
"""

NOW = datetime.now(timezone.utc)


def _make_pre_m1_db(db_file: str) -> None:
    """Create a database that looks like a pre-M1 deployment volume."""
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(_PRE_M1_TABLES)
        conn.execute(
            "INSERT INTO orders (id, user_id, ticker, side, quantity, limit_price,"
            " status, created_at) VALUES ('old-order', 'default', 'AAPL', 'buy',"
            " 2, 150.0, 'open', ?)",
            (NOW.isoformat(),),
        )
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price,"
            " executed_at) VALUES ('old-trade', 'default', 'AAPL', 'buy', 1,"
            " 190.0, ?)",
            (NOW.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()


def _columns(db_file: str, table: str) -> dict[str, dict]:
    conn = get_conn(db_file)
    try:
        return {
            row["name"]: dict(row)
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
    finally:
        conn.close()


def _insert_order(
    db_file: str,
    ticker: str,
    side: str,
    quantity: float,
    *,
    kind: str = "limit",
    limit_price: float | None = None,
    stop_price: float | None = None,
    time_in_force: str = "gtc",
    expires_at: str | None = None,
    triggered_at: str | None = None,
) -> str:
    """Insert an open order row directly (for fill-loop unit tests)."""
    order_id = str(uuid.uuid4())
    conn = get_conn(db_file)
    try:
        conn.execute(
            """
            INSERT INTO orders (id, user_id, ticker, side, quantity, kind,
                limit_price, stop_price, time_in_force, expires_at,
                triggered_at, status, created_at)
            VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (order_id, ticker, side, quantity, kind, limit_price, stop_price,
             time_in_force, expires_at, triggered_at,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def _order_row(db_file: str, order_id: str) -> dict | None:
    conn = get_conn(db_file)
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _trades(db_file: str) -> list[dict]:
    conn = get_conn(db_file)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM trades").fetchall()]
    finally:
        conn.close()


def _cash(db_file: str) -> float:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = 'default'"
        ).fetchone()["cash_balance"]
    finally:
        conn.close()


def _buy_position(db_file: str, cache: PriceCache, ticker: str, quantity: float) -> None:
    """Seed a position through the real trade path."""
    conn = get_conn(db_file)
    try:
        outcome = execute_trade_on_conn(conn, cache, ticker, "buy", quantity)
        assert outcome["status"] == "executed"
        conn.commit()
    finally:
        conn.close()


@pytest_asyncio.fixture
async def orders_env(tmp_path, monkeypatch):
    """App client (portfolio + orders routers) plus direct cache/DB access."""
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


class TestColumnMigration:
    """Pre-M1 database volumes upgrade in place on init_db (Task A)."""

    def test_old_schema_gains_new_columns(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_m1_db(db_file)

        # Sanity: old schema really lacks the M1 columns
        assert "kind" not in _columns(db_file, "orders")
        assert "commission" not in _columns(db_file, "trades")
        assert _columns(db_file, "orders")["limit_price"]["notnull"] == 1

        init_db(db_file)  # what app startup does

        orders_cols = _columns(db_file, "orders")
        for col in ("kind", "stop_price", "time_in_force", "expires_at", "triggered_at"):
            assert col in orders_cols, f"orders.{col} missing after migration"
        trades_cols = _columns(db_file, "trades")
        for col in ("commission", "realized_pnl"):
            assert col in trades_cols, f"trades.{col} missing after migration"
        # limit_price relaxed to nullable so stop orders can store NULL
        assert orders_cols["limit_price"]["notnull"] == 0

        # Existing rows preserved with pre-M1 semantics via column defaults
        conn = get_conn(db_file)
        try:
            order = conn.execute("SELECT * FROM orders WHERE id = 'old-order'").fetchone()
            assert order["kind"] == "limit"
            assert order["time_in_force"] == "gtc"
            assert order["stop_price"] is None
            assert order["expires_at"] is None
            assert order["triggered_at"] is None
            assert order["limit_price"] == 150.0
            assert order["status"] == "open"

            trade = conn.execute("SELECT * FROM trades WHERE id = 'old-trade'").fetchone()
            assert trade["commission"] == 0
            assert trade["realized_pnl"] is None
            assert trade["price"] == 190.0
        finally:
            conn.close()

    def test_migration_is_idempotent(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_m1_db(db_file)
        for _ in range(3):
            init_db(db_file)  # must never raise or duplicate columns

        assert list(_columns(db_file, "orders")).count("kind") == 1
        conn = get_conn(db_file)
        try:
            assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_endpoints_work_on_migrated_db(self, tmp_path, monkeypatch):
        """Stop orders (NULL limit_price) and trades insert fine post-migration."""
        db_file = str(tmp_path / "old.db")
        _make_pre_m1_db(db_file)
        monkeypatch.setenv("DB_PATH", db_file)
        init_db(db_file)

        price_cache = PriceCache()
        price_cache.update("AAPL", 190.0)
        test_app = FastAPI()
        test_app.include_router(create_portfolio_router(price_cache, db_file))
        test_app.include_router(create_orders_router(price_cache, db_file))
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            # A buy stop above the market rests open with a NULL limit_price
            resp = await client.post(
                "/api/portfolio/orders",
                json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                      "kind": "stop", "stop_price": 200.0},
            )
            assert resp.status_code == 200
            order = resp.json()["order"]
            assert order["kind"] == "stop"
            assert order["limit_price"] is None
            assert order["status"] == "open"

            # Market trade writes the new trades columns
            trade = await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
            )
            assert trade.status_code == 200
            assert trade.json()["commission"] == 0.0
            assert trade.json()["realized_pnl"] is None

            # Listings include the migrated (pre-M1) rows with the new keys
            listing = await client.get("/api/portfolio/orders")
            assert listing.status_code == 200
            for o in listing.json()["orders"]:
                assert set(o.keys()) == ORDER_JSON_KEYS


@pytest.mark.asyncio
class TestStopPlacementValidation:
    """POST /api/portfolio/orders validation matrix for stop kinds (Task B)."""

    async def test_wrong_side_stops_rejected(self, orders_env):
        """SELL stops must be below the bid, BUY stops above the ask."""
        price = orders_env.cache.get_price("AAPL")  # zero spread: bid == ask
        cases = [
            # (side, kind, stop, expected error)
            ("sell", "stop", price + 5, "Stop price must be below the market"),
            ("sell", "stop", price, "Stop price must be below the market"),  # == bid
            ("buy", "stop", price - 5, "Stop price must be above the market"),
            ("buy", "stop", price, "Stop price must be above the market"),  # == ask
            ("sell", "stop_limit", price + 5, "Stop price must be below the market"),
            ("buy", "stop_limit", price - 5, "Stop price must be above the market"),
        ]
        for side, kind, stop, error in cases:
            payload = {"ticker": "AAPL", "quantity": 1, "side": side,
                       "kind": kind, "stop_price": stop}
            if kind == "stop_limit":
                payload["limit_price"] = stop
            resp = await orders_env.client.post("/api/portfolio/orders", json=payload)
            assert resp.status_code == 400, (side, kind, stop)
            assert resp.json() == {"error": error}

    async def test_wrong_side_checked_against_quote_not_last(self, orders_env):
        """With a real spread, the bid gates sell stops and the ask buy stops."""
        orders_env.cache.update("AAPL", 100.0, bid=99.0, ask=101.0)
        # Sell stop at 99.5: below last (100) but NOT below the bid (99) → reject
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "sell",
                  "kind": "stop", "stop_price": 99.5},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Stop price must be below the market"}
        # Buy stop at 100.5: above last but NOT above the ask (101) → reject
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "kind": "stop", "stop_price": 100.5},
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Stop price must be above the market"}

    async def test_required_fields_matrix(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        cases = [
            # kind limit: limit_price required, stop_price forbidden
            ({"kind": "limit"}, "limit_price is required for kind 'limit'"),
            ({"kind": "limit", "limit_price": price - 50, "stop_price": price - 60},
             "stop_price is not allowed for kind 'limit'"),
            # kind stop: stop_price required, limit_price forbidden
            ({"kind": "stop"}, "stop_price is required for kind 'stop'"),
            ({"kind": "stop", "stop_price": price - 50, "limit_price": price - 60},
             "limit_price is not allowed for kind 'stop'"),
            # kind stop_limit: both required
            ({"kind": "stop_limit", "stop_price": price - 50},
             "kind 'stop_limit' requires both limit_price and stop_price"),
            ({"kind": "stop_limit", "limit_price": price - 50},
             "kind 'stop_limit' requires both limit_price and stop_price"),
            ({"kind": "stop_limit"},
             "kind 'stop_limit' requires both limit_price and stop_price"),
        ]
        for extra, error in cases:
            payload = {"ticker": "AAPL", "quantity": 1, "side": "sell", **extra}
            resp = await orders_env.client.post("/api/portfolio/orders", json=payload)
            assert resp.status_code == 400, extra
            assert resp.json() == {"error": error}

    async def test_enum_and_positivity_validation(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        cases = [
            ({"kind": "market", "limit_price": price},
             "kind must be one of 'limit', 'stop', 'stop_limit'"),
            ({"kind": "limit", "limit_price": price - 50, "time_in_force": "ioc"},
             "time_in_force must be 'day' or 'gtc'"),
            ({"kind": "stop", "stop_price": 0}, "Stop price must be greater than 0"),
            ({"kind": "stop", "stop_price": -5}, "Stop price must be greater than 0"),
            ({"kind": "stop_limit", "stop_price": price - 50, "limit_price": 0},
             "Limit price must be greater than 0"),
        ]
        for extra, error in cases:
            payload = {"ticker": "AAPL", "quantity": 1, "side": "sell", **extra}
            resp = await orders_env.client.post("/api/portfolio/orders", json=payload)
            assert resp.status_code == 400, extra
            assert resp.json() == {"error": error}

        # Nothing was stored by any failed placement in this class
        conn = get_conn(orders_env.db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        finally:
            conn.close()

    async def test_valid_sell_stop_rests_open(self, orders_env):
        """A protective sell stop below the market rests open, never fills at placement."""
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 2, "side": "sell",
                  "kind": "stop", "stop_price": price - 20},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert set(order.keys()) == ORDER_JSON_KEYS
        assert order["kind"] == "stop"
        assert order["stop_price"] == price - 20
        assert order["limit_price"] is None
        assert order["status"] == "open"
        assert order["triggered_at"] is None
        assert order["time_in_force"] == "gtc"
        assert order["expires_at"] is None
        assert _trades(orders_env.db) == []  # no fill at placement

    async def test_valid_buy_stop_and_stop_limit_rest_open(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        stop = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "kind": "stop", "stop_price": price + 10},
        )
        assert stop.status_code == 200
        assert stop.json()["order"]["status"] == "open"

        stop_limit = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "kind": "stop_limit", "stop_price": price + 10,
                  "limit_price": price + 12},
        )
        assert stop_limit.status_code == 200
        order = stop_limit.json()["order"]
        assert order["kind"] == "stop_limit"
        assert order["stop_price"] == price + 10
        assert order["limit_price"] == price + 12
        assert order["status"] == "open"
        assert order["triggered_at"] is None

    async def test_day_tif_sets_expires_at_24h(self, orders_env):
        """'day' stamps expires_at = created_at + 24h (session close post-M3)."""
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "limit_price": price - 50, "time_in_force": "day"},
        )
        assert resp.status_code == 200
        order = resp.json()["order"]
        assert order["time_in_force"] == "day"
        created = datetime.fromisoformat(order["created_at"])
        expires = datetime.fromisoformat(order["expires_at"])
        assert expires - created == timedelta(hours=24)
        # Stored on the row too
        row = _order_row(orders_env.db, order["id"])
        assert row["expires_at"] == order["expires_at"]

    async def test_gtc_has_null_expires_at(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        resp = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "limit_price": price - 50, "time_in_force": "gtc"},
        )
        assert resp.json()["order"]["expires_at"] is None


class TestStopFillLoop:
    """Stop trigger + market-on-trigger fill semantics in process_open_orders_once."""

    def test_sell_stop_stays_open_while_bid_above_stop(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        _buy_position(fill_env.db, fill_env.cache, "AAPL", 2)
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 2, kind="stop", stop_price=150.0
        )

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "open"
        assert row["triggered_at"] is None
        assert len(_trades(fill_env.db)) == 1  # only the seeding buy

    def test_sell_stop_triggers_on_bid_and_fills_at_bid(self, fill_env):
        """Stop-loss: bid drops to/below the stop → market fill at the bid."""
        fill_env.cache.update("AAPL", 200.0)
        _buy_position(fill_env.db, fill_env.cache, "AAPL", 2)
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 2, kind="stop", stop_price=150.0
        )

        # Bid crosses the stop; distinct bid/ask prove the fill uses the BID
        fill_env.cache.update("AAPL", 150.0, bid=149.5, ask=150.5)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}

        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 149.5
        assert row["triggered_at"] is not None
        assert row["filled_at"] is not None
        assert row["fill_trade_id"] is not None
        sells = [t for t in _trades(fill_env.db) if t["side"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["price"] == 149.5
        assert _cash(fill_env.db) == pytest.approx(10000.0 - 2 * 200.0 + 2 * 149.5)

    def test_buy_stop_triggers_on_ask_and_fills_at_ask(self, fill_env):
        """Breakout entry: ask rises to/above the stop → market fill at the ask."""
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_order(
            fill_env.db, "AAPL", "buy", 2, kind="stop", stop_price=210.0
        )

        # Ask below the stop → untriggered
        fill_env.cache.update("AAPL", 209.0, bid=208.5, ask=209.5)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        assert _order_row(fill_env.db, order_id)["status"] == "open"

        # Ask crosses the stop → fills at the ASK
        fill_env.cache.update("AAPL", 210.0, bid=209.5, ask=210.5)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}

        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 210.5
        assert row["triggered_at"] is not None
        assert _cash(fill_env.db) == pytest.approx(10000.0 - 2 * 210.5)

    def test_triggered_stop_without_shares_rejects(self, fill_env):
        """A stop-loss with no position behind it rejects at trigger time."""
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 5, kind="stop", stop_price=150.0
        )
        fill_env.cache.update("AAPL", 149.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 1, "skipped": 0, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "rejected"
        assert row["reject_reason"] == "Insufficient shares to sell"
        assert row["fill_price"] is None


class TestStopLimit:
    """Stop-limit: trigger stamps triggered_at, then normal limit semantics."""

    def test_triggers_then_rests_as_limit(self, fill_env):
        """Trigger fires but limit not satisfied → triggered_at stamped, stays open."""
        fill_env.cache.update("AAPL", 200.0)
        _buy_position(fill_env.db, fill_env.cache, "AAPL", 2)
        # Sell if price falls to 150, but no worse than 152 (limit above trigger:
        # can only fill on a recovery).
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 2,
            kind="stop_limit", stop_price=150.0, limit_price=152.0,
        )

        fill_env.cache.update("AAPL", 150.0)  # bid 150 <= stop 150 → triggers
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        # Triggered but 150 < limit 152 → rests as an open limit order
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "open"
        assert row["triggered_at"] is not None
        first_trigger = row["triggered_at"]
        assert len(_trades(fill_env.db)) == 1  # only the seeding buy

        # Still resting on the next pass; triggered_at is stamped only once
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 1, "expired": 0}
        assert _order_row(fill_env.db, order_id)["triggered_at"] == first_trigger

        # Price recovers to the limit → fills with normal limit semantics (at bid)
        fill_env.cache.update("AAPL", 152.0)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 152.0
        assert row["triggered_at"] == first_trigger

    def test_trigger_and_fill_same_pass_when_limit_satisfied(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        _buy_position(fill_env.db, fill_env.cache, "AAPL", 2)
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 2,
            kind="stop_limit", stop_price=150.0, limit_price=148.0,
        )

        fill_env.cache.update("AAPL", 149.0)  # triggers (<=150) AND >= limit 148
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 149.0
        assert row["triggered_at"] is not None

    def test_buy_stop_limit_fills_at_ask_within_limit(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        order_id = _insert_order(
            fill_env.db, "AAPL", "buy", 1,
            kind="stop_limit", stop_price=210.0, limit_price=212.0,
        )

        fill_env.cache.update("AAPL", 211.0, bid=210.5, ask=211.5)
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        # ask 211.5 >= stop 210 triggers; ask 211.5 <= limit 212 fills at the ask
        assert counts == {"filled": 1, "rejected": 0, "skipped": 0, "expired": 0}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "filled"
        assert row["fill_price"] == 211.5


class TestTimeInForce:
    """Day orders expire via the fill loop; gtc orders never do."""

    def test_day_order_past_expiry_becomes_expired(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        order_id = _insert_order(
            fill_env.db, "AAPL", "buy", 1, limit_price=150.0,
            time_in_force="day", expires_at=past,
        )

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 0, "expired": 1}
        row = _order_row(fill_env.db, order_id)
        assert row["status"] == "expired"
        assert row["fill_price"] is None
        assert _trades(fill_env.db) == []

        # Terminal: subsequent passes ignore it
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 0, "expired": 0}

    def test_expiry_wins_over_marketability(self, fill_env):
        """An expired order does NOT fill even if it is marketable right now."""
        fill_env.cache.update("AAPL", 140.0)  # marketable for a buy limit @150
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        order_id = _insert_order(
            fill_env.db, "AAPL", "buy", 1, limit_price=150.0,
            time_in_force="day", expires_at=past,
        )

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 0, "expired": 1}
        assert _order_row(fill_env.db, order_id)["status"] == "expired"
        assert _trades(fill_env.db) == []

    def test_day_order_before_expiry_and_gtc_untouched(self, fill_env):
        fill_env.cache.update("AAPL", 200.0)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        day_id = _insert_order(
            fill_env.db, "AAPL", "buy", 1, limit_price=150.0,
            time_in_force="day", expires_at=future,
        )
        gtc_id = _insert_order(fill_env.db, "AAPL", "buy", 1, limit_price=150.0)

        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 2, "expired": 0}
        assert _order_row(fill_env.db, day_id)["status"] == "open"
        assert _order_row(fill_env.db, gtc_id)["status"] == "open"

    def test_day_stop_order_expires_too(self, fill_env):
        """Expiry applies to stop kinds as well, even while untriggered."""
        fill_env.cache.update("AAPL", 200.0)
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        order_id = _insert_order(
            fill_env.db, "AAPL", "sell", 1, kind="stop", stop_price=150.0,
            time_in_force="day", expires_at=past,
        )
        counts = process_open_orders_once(fill_env.db, fill_env.cache)
        assert counts == {"filled": 0, "rejected": 0, "skipped": 0, "expired": 1}
        assert _order_row(fill_env.db, order_id)["status"] == "expired"

    @pytest.mark.asyncio
    async def test_cancel_expired_order_returns_400(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        placed = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "limit_price": price - 50, "time_in_force": "day"},
        )
        order_id = placed.json()["order"]["id"]

        # Force the expiry into the past, then run the loop
        conn = get_conn(orders_env.db)
        try:
            conn.execute(
                "UPDATE orders SET expires_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), order_id),
            )
            conn.commit()
        finally:
            conn.close()
        counts = process_open_orders_once(orders_env.db, orders_env.cache)
        assert counts["expired"] == 1

        resp = await orders_env.client.delete(f"/api/portfolio/orders/{order_id}")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Order is not open"}

    @pytest.mark.asyncio
    async def test_status_filter_accepts_expired(self, orders_env):
        price = orders_env.cache.get_price("AAPL")
        placed = await orders_env.client.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy",
                  "limit_price": price - 50, "time_in_force": "day"},
        )
        order_id = placed.json()["order"]["id"]
        conn = get_conn(orders_env.db)
        try:
            conn.execute(
                "UPDATE orders SET expires_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), order_id),
            )
            conn.commit()
        finally:
            conn.close()
        process_open_orders_once(orders_env.db, orders_env.cache)

        resp = await orders_env.client.get("/api/portfolio/orders?status=expired")
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        assert [o["id"] for o in orders] == [order_id]
        assert orders[0]["status"] == "expired"

        # 'expired' orders are excluded from the open filter
        open_resp = await orders_env.client.get("/api/portfolio/orders?status=open")
        assert open_resp.json()["orders"] == []
