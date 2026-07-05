"""Tests for portfolio API endpoints.

Covers:
- GET /api/portfolio
- POST /api/portfolio/trade (buy and sell)
- GET /api/portfolio/trades (trade blotter)
- GET /api/portfolio/history
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestPortfolioEndpoints:
    """Integration tests for the portfolio API routes."""

    async def test_get_portfolio_fresh_db(self, app_client):
        """Fresh DB: cash=10000.0, positions=[], total_value=10000.0."""
        response = await app_client.get("/api/portfolio/")
        assert response.status_code == 200
        data = response.json()
        assert data["cash"] == 10000.0
        assert data["positions"] == []
        assert data["total_value"] == 10000.0

    async def test_trade_buy_reduces_cash(self, app_client):
        """Buying 1 share of AAPL reduces cash and creates a position."""
        buy_resp = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert buy_resp.status_code == 200
        assert buy_resp.json()["status"] == "ok"

        portfolio = await app_client.get("/api/portfolio/")
        data = portfolio.json()

        # Cash must be less than the initial 10000
        assert data["cash"] < 10000.0

        # AAPL position must exist
        tickers = [p["ticker"] for p in data["positions"]]
        assert "AAPL" in tickers

        # Quantity should be 1
        aapl = next(p for p in data["positions"] if p["ticker"] == "AAPL")
        assert aapl["quantity"] == 1.0

    async def test_trade_buy_insufficient_cash(self, app_client):
        """Buying more than available cash returns 400 with error message."""
        # AAPL seed price is $190; buying 1_000_000 shares exceeds $10k balance
        response = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1_000_000, "side": "buy"},
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Insufficient cash"}

    async def test_trade_sell_without_position(self, app_client):
        """Selling a ticker not held returns 400."""
        response = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "sell"},
        )
        assert response.status_code == 400
        body = response.json()
        assert "error" in body

    async def test_trade_buy_then_sell(self, app_client):
        """Buy 2 shares then sell 1 — position quantity should be 1."""
        # Buy 2
        buy_resp = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 2, "side": "buy"},
        )
        assert buy_resp.status_code == 200

        # Sell 1
        sell_resp = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "sell"},
        )
        assert sell_resp.status_code == 200

        portfolio = await app_client.get("/api/portfolio/")
        positions = portfolio.json()["positions"]
        aapl = next((p for p in positions if p["ticker"] == "AAPL"), None)
        assert aapl is not None
        assert aapl["quantity"] == 1.0

    async def test_portfolio_history_empty(self, app_client):
        """Fresh DB: GET /api/portfolio/history returns empty snapshots list."""
        response = await app_client.get("/api/portfolio/history")
        assert response.status_code == 200
        data = response.json()
        assert "snapshots" in data
        assert data["snapshots"] == []

    async def test_portfolio_history_after_trade(self, app_client):
        """After a buy, at least one snapshot is recorded."""
        await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )

        response = await app_client.get("/api/portfolio/history")
        data = response.json()
        assert len(data["snapshots"]) >= 1
        snapshot = data["snapshots"][0]
        assert "total_value" in snapshot
        assert "recorded_at" in snapshot


@pytest.mark.asyncio
class TestTradesEndpoint:
    """Integration tests for GET /api/portfolio/trades (trade blotter)."""

    @staticmethod
    async def _execute_three_trades(app_client):
        """Buy AAPL, buy GOOGL, sell AAPL — in that order."""
        for payload in (
            {"ticker": "AAPL", "quantity": 2, "side": "buy"},
            {"ticker": "GOOGL", "quantity": 1, "side": "buy"},
            {"ticker": "AAPL", "quantity": 1, "side": "sell"},
        ):
            resp = await app_client.post("/api/portfolio/trade", json=payload)
            assert resp.status_code == 200

    async def test_trades_empty_fresh_db(self, app_client):
        """Fresh DB: GET /api/portfolio/trades returns an empty list."""
        response = await app_client.get("/api/portfolio/trades")
        assert response.status_code == 200
        assert response.json() == {"trades": []}

    async def test_trades_newest_first_with_correct_shape(self, app_client):
        """After several trades, entries come back newest-first with all keys."""
        await self._execute_three_trades(app_client)

        response = await app_client.get("/api/portfolio/trades")
        assert response.status_code == 200
        trades = response.json()["trades"]
        assert len(trades) == 3

        # Exact response shape per trade entry
        for trade in trades:
            assert set(trade.keys()) == {
                "id", "ticker", "side", "quantity", "price", "executed_at",
            }

        # Newest first: sell AAPL, buy GOOGL, buy AAPL
        assert (trades[0]["ticker"], trades[0]["side"]) == ("AAPL", "sell")
        assert (trades[1]["ticker"], trades[1]["side"]) == ("GOOGL", "buy")
        assert (trades[2]["ticker"], trades[2]["side"]) == ("AAPL", "buy")

        # executed_at is monotonically non-increasing
        executed = [t["executed_at"] for t in trades]
        assert executed == sorted(executed, reverse=True)

    async def test_trades_limit_returns_newest(self, app_client):
        """limit=2 returns only the 2 most recent trades."""
        await self._execute_three_trades(app_client)

        response = await app_client.get("/api/portfolio/trades?limit=2")
        assert response.status_code == 200
        trades = response.json()["trades"]
        assert len(trades) == 2
        assert (trades[0]["ticker"], trades[0]["side"]) == ("AAPL", "sell")
        assert (trades[1]["ticker"], trades[1]["side"]) == ("GOOGL", "buy")

    async def test_trades_limit_clamped_high(self, app_client):
        """limit above 500 is clamped, not rejected."""
        await self._execute_three_trades(app_client)

        response = await app_client.get("/api/portfolio/trades?limit=10000")
        assert response.status_code == 200
        assert len(response.json()["trades"]) == 3

    async def test_trades_limit_clamped_low(self, app_client):
        """limit below 1 is clamped up to 1."""
        await self._execute_three_trades(app_client)

        for bad_low in ("0", "-5"):
            response = await app_client.get(f"/api/portfolio/trades?limit={bad_low}")
            assert response.status_code == 200
            assert len(response.json()["trades"]) == 1

    async def test_trades_invalid_limit_returns_400(self, app_client):
        """Non-integer limit returns 400 with an error message."""
        for bad in ("abc", "2.5", ""):
            response = await app_client.get(f"/api/portfolio/trades?limit={bad}")
            assert response.status_code == 400
            assert "error" in response.json()
