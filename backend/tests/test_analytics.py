"""Tests for GET /api/portfolio/analytics (M3.4).

Uses the app_client fixture (isolated temp DB + cache seeded at SEED_PRICES).
Trades are executed through the public trade endpoint; the equity curve for
drawdown/Sharpe assertions is crafted by inserting portfolio_snapshots rows
directly. The drawdown/Sharpe math is also unit-tested on the pure helpers.
"""

from __future__ import annotations

import math
import uuid

import pytest

from app.db.connection import get_conn
from app.market.seed_prices import SEED_PRICES
from app.routes.portfolio import _max_drawdown_pct, _sharpe

ANALYTICS_KEYS = {
    "total_trades",
    "sell_trades",
    "win_rate",
    "realized_pnl",
    "max_drawdown_pct",
    "sharpe",
    "best_trade",
    "worst_trade",
    "sector_allocation",
}

TRADE_KEYS = {"ticker", "side", "quantity", "price", "realized_pnl", "executed_at"}


def _insert_snapshots(tmp_path, values: list[float]) -> None:
    """Insert a crafted equity curve, ascending recorded_at order."""
    conn = get_conn(str(tmp_path / "test.db"))
    try:
        for i, value in enumerate(values):
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                " VALUES (?, 'default', ?, ?)",
                (str(uuid.uuid4()), value, f"2026-07-06T10:{i:02d}:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()


async def _trade(app_client, ticker: str, side: str, quantity: float) -> dict:
    response = await app_client.post(
        "/api/portfolio/trade",
        json={"ticker": ticker, "side": side, "quantity": quantity},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestDrawdownHelper:
    """_max_drawdown_pct: peak-to-trough % as a positive number."""

    def test_fewer_than_two_snapshots_is_none(self):
        assert _max_drawdown_pct([]) is None
        assert _max_drawdown_pct([10000.0]) is None

    def test_crafted_curve(self):
        # Peak 10500 -> trough 9800: (10500-9800)/10500 = 6.6667%.
        dd = _max_drawdown_pct([10000.0, 10500.0, 9800.0, 10200.0])
        assert dd == pytest.approx(6.6667, abs=1e-3)

    def test_monotonic_curve_has_zero_drawdown(self):
        assert _max_drawdown_pct([10000.0, 10100.0, 10200.0]) == 0.0


class TestSharpeHelper:
    """_sharpe: mean/std of consecutive returns x sqrt(count)."""

    def test_below_ten_snapshots_is_none(self):
        assert _sharpe([10000.0 + i for i in range(9)]) is None

    def test_zero_std_is_none(self):
        assert _sharpe([10000.0] * 12) is None

    def test_finite_above_ten_snapshots(self):
        values = [10000.0]
        for i in range(11):
            values.append(values[-1] * (1.01 if i % 2 == 0 else 0.995))
        sharpe = _sharpe(values)
        assert sharpe is not None
        assert isinstance(sharpe, float)
        assert math.isfinite(sharpe)


@pytest.mark.asyncio
class TestAnalyticsEndpoint:
    """GET /api/portfolio/analytics contract."""

    async def test_empty_state_zeros_nulls_and_pure_cash(self, app_client):
        response = await app_client.get("/api/portfolio/analytics")
        assert response.status_code == 200
        data = response.json()

        assert set(data.keys()) == ANALYTICS_KEYS
        assert data["total_trades"] == 0
        assert data["sell_trades"] == 0
        assert data["win_rate"] is None
        assert data["realized_pnl"] == 0.0
        assert data["max_drawdown_pct"] is None  # fresh DB: no snapshots
        assert data["sharpe"] is None
        assert data["best_trade"] is None
        assert data["worst_trade"] is None
        assert data["sector_allocation"] == [
            {"sector": "cash", "value": 10000.0, "weight": 1.0}
        ]

    async def test_win_rate_best_worst_and_realized(
        self, app_client, fake_market_source
    ):
        cache = fake_market_source.price_cache

        # Winning round-trip: buy 10 AAPL @190, sell after a move to 200 (+100).
        await _trade(app_client, "AAPL", "buy", 10)
        cache.update("AAPL", 200.0)
        await _trade(app_client, "AAPL", "sell", 10)

        # Losing round-trip: buy 5 MSFT @420, sell after a drop to 410 (-50).
        await _trade(app_client, "MSFT", "buy", 5)
        cache.update("MSFT", 410.0)
        await _trade(app_client, "MSFT", "sell", 5)

        response = await app_client.get("/api/portfolio/analytics")
        data = response.json()

        assert data["total_trades"] == 4
        assert data["sell_trades"] == 2
        assert data["win_rate"] == 0.5  # 1 win of 2 sells with realized_pnl
        assert data["realized_pnl"] == pytest.approx(50.0)

        assert set(data["best_trade"].keys()) == TRADE_KEYS
        assert data["best_trade"]["ticker"] == "AAPL"
        assert data["best_trade"]["side"] == "sell"
        assert data["best_trade"]["quantity"] == 10
        assert data["best_trade"]["price"] == 200.0
        assert data["best_trade"]["realized_pnl"] == pytest.approx(100.0)

        assert set(data["worst_trade"].keys()) == TRADE_KEYS
        assert data["worst_trade"]["ticker"] == "MSFT"
        assert data["worst_trade"]["realized_pnl"] == pytest.approx(-50.0)

    async def test_buys_alone_leave_win_rate_null(self, app_client):
        await _trade(app_client, "AAPL", "buy", 1)

        response = await app_client.get("/api/portfolio/analytics")
        data = response.json()
        assert data["total_trades"] == 1
        assert data["sell_trades"] == 0
        assert data["win_rate"] is None
        assert data["best_trade"] is None
        assert data["worst_trade"] is None

    async def test_drawdown_from_crafted_snapshot_sequence(
        self, app_client, tmp_path
    ):
        _insert_snapshots(tmp_path, [10000.0, 10500.0, 9800.0, 10200.0])

        response = await app_client.get("/api/portfolio/analytics")
        data = response.json()
        assert data["max_drawdown_pct"] == pytest.approx(6.6667, abs=1e-3)
        assert data["sharpe"] is None  # 4 snapshots < 10

    async def test_sharpe_finite_at_ten_plus_snapshots(self, app_client, tmp_path):
        values = [10000.0]
        for i in range(11):
            values.append(round(values[-1] * (1.008 if i % 3 else 0.997), 2))
        _insert_snapshots(tmp_path, values)

        response = await app_client.get("/api/portfolio/analytics")
        sharpe = response.json()["sharpe"]
        assert sharpe is not None
        assert isinstance(sharpe, float)
        assert math.isfinite(sharpe)

    async def test_sector_allocation_groups_and_weights(
        self, app_client, fake_market_source
    ):
        # tech 1900 (10 AAPL @190), financials 390 (2 JPM @195),
        # crypto 650 (0.01 BTC @65000), cash 7060 — total exactly 10000.
        await _trade(app_client, "AAPL", "buy", 10)
        await _trade(app_client, "JPM", "buy", 2)
        await _trade(app_client, "BTC", "buy", 0.01)
        assert SEED_PRICES["AAPL"] == 190.0  # guard: expectations track seeds

        response = await app_client.get("/api/portfolio/analytics")
        allocation = response.json()["sector_allocation"]

        assert [row["sector"] for row in allocation] == [
            "cash", "tech", "crypto", "financials",
        ]  # sorted by value desc
        by_sector = {row["sector"]: row for row in allocation}
        assert by_sector["cash"]["value"] == pytest.approx(7060.0)
        assert by_sector["tech"]["value"] == pytest.approx(1900.0)
        assert by_sector["crypto"]["value"] == pytest.approx(650.0)
        assert by_sector["financials"]["value"] == pytest.approx(390.0)

        assert by_sector["tech"]["weight"] == pytest.approx(0.19)
        assert sum(row["weight"] for row in allocation) == pytest.approx(1.0)
