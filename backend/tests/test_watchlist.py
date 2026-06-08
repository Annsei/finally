"""Tests for watchlist API endpoints.

Covers:
- GET /api/watchlist
- POST /api/watchlist
- DELETE /api/watchlist/{ticker}
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestWatchlistEndpoints:
    """Integration tests for the watchlist API routes."""

    async def test_get_watchlist_returns_10_default_tickers(self, app_client):
        """Fresh DB: GET /api/watchlist returns exactly 10 seeded tickers."""
        response = await app_client.get("/api/watchlist/")
        assert response.status_code == 200
        data = response.json()
        assert "tickers" in data
        assert len(data["tickers"]) == 10

    async def test_add_ticker(self, app_client):
        """POST a new ticker and verify it appears in GET response."""
        add_resp = await app_client.post("/api/watchlist/", json={"ticker": "PYPL"})
        assert add_resp.status_code == 200
        assert add_resp.json()["status"] == "ok"
        assert add_resp.json()["ticker"] == "PYPL"

        get_resp = await app_client.get("/api/watchlist/")
        tickers = [t["ticker"] for t in get_resp.json()["tickers"]]
        assert "PYPL" in tickers

    async def test_add_ticker_uppercase_normalization(self, app_client):
        """Lowercase ticker input is normalized to uppercase."""
        add_resp = await app_client.post("/api/watchlist/", json={"ticker": "pypl"})
        assert add_resp.status_code == 200
        assert add_resp.json()["ticker"] == "PYPL"

        get_resp = await app_client.get("/api/watchlist/")
        tickers = [t["ticker"] for t in get_resp.json()["tickers"]]
        assert "PYPL" in tickers
        assert "pypl" not in tickers

    async def test_add_existing_ticker_idempotent(self, app_client):
        """Adding AAPL (already in seed data) twice returns 200 with no duplicate."""
        resp1 = await app_client.post("/api/watchlist/", json={"ticker": "AAPL"})
        assert resp1.status_code == 200

        resp2 = await app_client.post("/api/watchlist/", json={"ticker": "AAPL"})
        assert resp2.status_code == 200

        get_resp = await app_client.get("/api/watchlist/")
        tickers = [t["ticker"] for t in get_resp.json()["tickers"]]
        assert tickers.count("AAPL") == 1

    async def test_remove_ticker(self, app_client):
        """Add PYPL, then DELETE it — it should no longer appear in GET."""
        await app_client.post("/api/watchlist/", json={"ticker": "PYPL"})

        del_resp = await app_client.delete("/api/watchlist/PYPL")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "ok"

        get_resp = await app_client.get("/api/watchlist/")
        tickers = [t["ticker"] for t in get_resp.json()["tickers"]]
        assert "PYPL" not in tickers

    async def test_remove_nonexistent_ticker(self, app_client):
        """DELETE a ticker not in watchlist returns 200 (idempotent)."""
        response = await app_client.delete("/api/watchlist/NOTEXIST")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_watchlist_has_price_fields(self, app_client):
        """Each ticker in GET /api/watchlist response has price, change_percent, direction keys."""
        response = await app_client.get("/api/watchlist/")
        assert response.status_code == 200
        tickers = response.json()["tickers"]
        assert len(tickers) > 0
        for entry in tickers:
            assert "price" in entry
            assert "change_percent" in entry
            assert "direction" in entry
