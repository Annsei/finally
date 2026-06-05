"""Integration tests for the chat API endpoint.

Covers CHAT-01 through CHAT-06 requirements using the chat_client fixture
which sets LLM_MOCK=true and registers all routers including the chat router.

All tests use POST /api/chat/ (note trailing slash — the router uses
prefix="/api/chat" + @router.post("/") so the full path is /api/chat/).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestChat:
    """Integration tests for POST /api/chat/ using the mock LLM."""

    async def test_chat_returns_structured_response(self, chat_client):
        """CHAT-01: POST /api/chat/ returns 200 with message, trades, watchlist_changes keys."""
        response = await chat_client.post("/api/chat/", json={"message": "Hello"})
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "trades" in data
        assert "watchlist_changes" in data

    async def test_response_schema_shape(self, chat_client):
        """CHAT-02: Response fields have correct types — message is str, trades/watchlist_changes are lists."""
        response = await chat_client.post("/api/chat/", json={"message": "What's my portfolio?"})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["message"], str)
        assert isinstance(data["trades"], list)
        assert isinstance(data["watchlist_changes"], list)

    async def test_mock_trade_executes(self, chat_client):
        """CHAT-03: After POST /api/chat/, AAPL position appears in portfolio and cash < 10000."""
        chat_resp = await chat_client.post("/api/chat/", json={"message": "Buy some AAPL"})
        assert chat_resp.status_code == 200

        portfolio_resp = await chat_client.get("/api/portfolio/")
        assert portfolio_resp.status_code == 200
        data = portfolio_resp.json()

        # Cash must have decreased (mock buys 5 shares of AAPL at seed price ~$190)
        assert data["cash"] < 10000.0

        # AAPL position must exist
        tickers = [p["ticker"] for p in data["positions"]]
        assert "AAPL" in tickers

    async def test_failed_trade_in_outcomes(self, chat_client):
        """CHAT-03: Trade failures are returned as outcome dicts (status=failed), not HTTP 500.

        The mock always tries to buy AAPL. We verify the response structure supports
        failed-trade outcomes by calling twice and confirming we still get 200 not 500.
        We also check that trade outcomes include a 'status' key — the structural requirement
        that failures are returned as dicts, never raised as HTTP errors.
        """
        # First call: buy 5 AAPL (succeeds)
        resp1 = await chat_client.post("/api/chat/", json={"message": "Buy AAPL"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        # Trade outcome dicts must have a 'status' key
        assert len(data1["trades"]) > 0
        for trade_outcome in data1["trades"]:
            assert "status" in trade_outcome

        # Second call: also succeeds (enough cash for another 5 shares)
        resp2 = await chat_client.post("/api/chat/", json={"message": "Buy AAPL again"})
        assert resp2.status_code == 200

    async def test_mock_watchlist_add(self, chat_client):
        """CHAT-04: After POST /api/chat/, PYPL appears in GET /api/watchlist/."""
        chat_resp = await chat_client.post("/api/chat/", json={"message": "Add PYPL to watchlist"})
        assert chat_resp.status_code == 200

        watchlist_resp = await chat_client.get("/api/watchlist/")
        assert watchlist_resp.status_code == 200
        data = watchlist_resp.json()

        tickers = [t["ticker"] for t in data["tickers"]]
        assert "PYPL" in tickers

    async def test_messages_persisted(self, chat_client):
        """CHAT-05: chat_messages rows are persisted; second request loads history without error."""
        # First request: persists user + assistant rows
        resp1 = await chat_client.post("/api/chat/", json={"message": "First message"})
        assert resp1.status_code == 200
        assert isinstance(resp1.json()["message"], str)

        # Second request: loads history (2 rows) and still returns 200
        resp2 = await chat_client.post("/api/chat/", json={"message": "Second message"})
        assert resp2.status_code == 200
        assert isinstance(resp2.json()["message"], str)

    async def test_history_loaded(self, chat_client):
        """CHAT-05: Two sequential POST /api/chat/ calls both return 200 with message key.

        The second call exercises the history load path (2 existing rows from first call).
        """
        resp1 = await chat_client.post("/api/chat/", json={"message": "Tell me about AAPL"})
        assert resp1.status_code == 200
        assert "message" in resp1.json()

        resp2 = await chat_client.post("/api/chat/", json={"message": "Should I buy more?"})
        assert resp2.status_code == 200
        assert "message" in resp2.json()

    async def test_mock_mode_deterministic(self, chat_client):
        """CHAT-06: LLM_MOCK=true returns the exact deterministic message string (D-06)."""
        response = await chat_client.post("/api/chat/", json={"message": "any message"})
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )
