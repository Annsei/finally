"""Tests for GET /api/market/history (chart history backfill).

Covers query-param validation, uppercase normalization, unknown tickers,
ascending order, limit semantics, and that bars reflect ticks written through
the shared PriceCache funnel (including the FakeMarketSource seed).
"""

from __future__ import annotations

import pytest


def _seed_bars(fake_market_source, ticker: str, base_ts: int, count: int) -> None:
    """Write ``count`` one-per-second ticks for a fresh ticker.

    Uses explicit timestamps so bar bucketing is deterministic. The ticker
    must be new to the cache (no newer bars to collide with).
    """
    cache = fake_market_source.price_cache
    for i in range(count):
        cache.update(ticker, 100.0 + i, timestamp=float(base_ts + i), volume=10.0 * (i + 1))


@pytest.mark.asyncio
class TestMarketHistoryValidation:
    """Parameter validation for GET /api/market/history."""

    async def test_missing_ticker_returns_400(self, app_client):
        response = await app_client.get("/api/market/history")
        assert response.status_code == 400
        assert "error" in response.json()

    async def test_empty_ticker_returns_400(self, app_client):
        for empty in ("", "%20%20"):
            response = await app_client.get(f"/api/market/history?ticker={empty}")
            assert response.status_code == 400
            assert "error" in response.json()

    async def test_non_integer_limit_returns_400(self, app_client):
        for bad in ("abc", "2.5", ""):
            response = await app_client.get(f"/api/market/history?ticker=AAPL&limit={bad}")
            assert response.status_code == 400
            assert "error" in response.json()


@pytest.mark.asyncio
class TestMarketHistoryResponses:
    """Response shape and bar semantics."""

    async def test_unknown_ticker_returns_empty_bars(self, app_client):
        response = await app_client.get("/api/market/history?ticker=NOPE")
        assert response.status_code == 200
        assert response.json() == {"ticker": "NOPE", "bars": []}

    async def test_ticker_uppercase_normalized(self, app_client, fake_market_source):
        _seed_bars(fake_market_source, "ZINC", base_ts=1_700_000_000, count=1)
        response = await app_client.get("/api/market/history?ticker=zinc")
        assert response.status_code == 200
        data = response.json()
        assert data["ticker"] == "ZINC"
        assert len(data["bars"]) == 1

    async def test_bar_shape_and_values(self, app_client, fake_market_source):
        """Bars carry exactly time/open/high/low/close/volume with exact math."""
        cache = fake_market_source.price_cache
        base = 1_700_000_000
        cache.update("ZINC", 100.0, timestamp=float(base) + 0.2, volume=10.0)
        cache.update("ZINC", 102.0, timestamp=float(base) + 0.5, volume=5.0)
        cache.update("ZINC", 99.0, timestamp=float(base) + 0.9, volume=2.0)

        response = await app_client.get("/api/market/history?ticker=ZINC")
        bars = response.json()["bars"]
        assert len(bars) == 1
        assert bars[0] == {
            "time": base,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 99.0,
            "volume": 17.0,
        }

    async def test_bars_ascending_by_time(self, app_client, fake_market_source):
        _seed_bars(fake_market_source, "ZINC", base_ts=1_700_000_000, count=5)
        response = await app_client.get("/api/market/history?ticker=ZINC")
        times = [bar["time"] for bar in response.json()["bars"]]
        assert times == sorted(times)
        assert len(times) == 5

    async def test_limit_returns_most_recent(self, app_client, fake_market_source):
        base = 1_700_000_000
        _seed_bars(fake_market_source, "ZINC", base_ts=base, count=5)
        response = await app_client.get("/api/market/history?ticker=ZINC&limit=2")
        bars = response.json()["bars"]
        assert [bar["time"] for bar in bars] == [base + 3, base + 4]

    async def test_limit_clamped(self, app_client, fake_market_source):
        _seed_bars(fake_market_source, "ZINC", base_ts=1_700_000_000, count=3)
        # Above the cap: clamped to 7200, not rejected
        high = await app_client.get("/api/market/history?ticker=ZINC&limit=999999")
        assert high.status_code == 200
        assert len(high.json()["bars"]) == 3
        # Below 1: clamped up to 1
        for low in ("0", "-5"):
            response = await app_client.get(f"/api/market/history?ticker=ZINC&limit={low}")
            assert response.status_code == 200
            assert len(response.json()["bars"]) == 1

    async def test_bars_reflect_fake_source_ticks(self, app_client, fake_market_source):
        """Adding a watchlist ticker seeds the cache (fake source) → bar appears."""
        add = await app_client.post("/api/watchlist/", json={"ticker": "PYPL"})
        assert add.status_code == 200

        response = await app_client.get("/api/market/history?ticker=PYPL")
        assert response.status_code == 200
        bars = response.json()["bars"]
        assert len(bars) == 1
        assert bars[0]["close"] == fake_market_source.SEED_PRICE

    async def test_seeded_default_tickers_have_bars(self, app_client):
        """Every cache write funnels into history — seeded tickers have a bar."""
        response = await app_client.get("/api/market/history?ticker=AAPL")
        assert response.status_code == 200
        bars = response.json()["bars"]
        assert len(bars) >= 1
        assert set(bars[0].keys()) == {"time", "open", "high", "low", "close", "volume"}
