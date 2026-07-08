"""Tests for the optional ``ticker`` filter on GET /api/portfolio/trades (P1 §3.5).

Covers the exact-match filter, uppercase normalization, blank-value
handling, interaction with ``limit``, and — the P1 hard gate — a regression
asserting the default (no ``ticker``) response is byte-identical to the
pre-P1 blotter shape.
"""

from __future__ import annotations

import pytest


async def _trade(app_client, ticker: str, quantity: float, side: str = "buy") -> dict:
    response = await app_client.post(
        "/api/portfolio/trade",
        json={"ticker": ticker, "quantity": quantity, "side": side},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
class TestTradesTickerFilter:
    """GET /api/portfolio/trades?ticker=... semantics."""

    async def test_filters_to_exact_ticker(self, app_client):
        await _trade(app_client, "AAPL", 1)
        await _trade(app_client, "MSFT", 2)
        await _trade(app_client, "AAPL", 3)

        response = await app_client.get("/api/portfolio/trades?ticker=AAPL")
        assert response.status_code == 200
        trades = response.json()["trades"]
        assert len(trades) == 2
        assert all(t["ticker"] == "AAPL" for t in trades)
        # Newest first is preserved under the filter.
        assert [t["quantity"] for t in trades] == [3, 1]

    async def test_ticker_is_uppercase_normalized(self, app_client):
        await _trade(app_client, "AAPL", 1)
        await _trade(app_client, "MSFT", 2)

        response = await app_client.get("/api/portfolio/trades?ticker=aapl")
        trades = response.json()["trades"]
        assert len(trades) == 1
        assert trades[0]["ticker"] == "AAPL"

    async def test_unknown_ticker_returns_empty_list(self, app_client):
        await _trade(app_client, "AAPL", 1)
        response = await app_client.get("/api/portfolio/trades?ticker=NOPE")
        assert response.status_code == 200
        assert response.json() == {"trades": []}

    async def test_blank_ticker_treated_as_absent(self, app_client):
        await _trade(app_client, "AAPL", 1)
        await _trade(app_client, "MSFT", 2)
        response = await app_client.get("/api/portfolio/trades?ticker=")
        assert response.status_code == 200
        assert len(response.json()["trades"]) == 2

    async def test_ticker_combines_with_limit(self, app_client):
        for quantity in (1, 2, 3):
            await _trade(app_client, "AAPL", quantity)
        await _trade(app_client, "MSFT", 9)

        response = await app_client.get("/api/portfolio/trades?ticker=AAPL&limit=2")
        trades = response.json()["trades"]
        assert [t["quantity"] for t in trades] == [3, 2]  # 2 newest AAPL trades

    async def test_non_integer_limit_still_400_with_ticker(self, app_client):
        response = await app_client.get("/api/portfolio/trades?ticker=AAPL&limit=abc")
        assert response.status_code == 400
        assert "error" in response.json()


@pytest.mark.asyncio
class TestTradesDefaultRegression:
    """P1 hard gate: default GET /api/portfolio/trades is byte-identical."""

    async def test_default_response_exactly_pre_p1_shape(self, app_client):
        buy = await _trade(app_client, "AAPL", 2)
        sell = await _trade(app_client, "AAPL", 1, side="sell")

        response = await app_client.get("/api/portfolio/trades")
        assert response.status_code == 200
        body = response.json()

        # Full-body equality: exact keys, exact values, newest first.
        conn_rows = body["trades"]
        assert body == {
            "trades": [
                {
                    "id": sell["trade_id"],
                    "ticker": "AAPL",
                    "side": "sell",
                    "quantity": 1,
                    "price": sell["price"],
                    "commission": 0,
                    "realized_pnl": sell["realized_pnl"],
                    "executed_at": conn_rows[0]["executed_at"],
                },
                {
                    "id": buy["trade_id"],
                    "ticker": "AAPL",
                    "side": "buy",
                    "quantity": 2,
                    "price": buy["price"],
                    "commission": 0,
                    "realized_pnl": None,
                    "executed_at": conn_rows[1]["executed_at"],
                },
            ]
        }
        # Serialized key ORDER is part of the byte contract.
        for trade in conn_rows:
            assert list(trade.keys()) == [
                "id", "ticker", "side", "quantity", "price", "commission",
                "realized_pnl", "executed_at",
            ]
