"""Correlation heatmap endpoint tests (P4 §2).

Covers the Pearson helper on exact series (±1, orthogonal 0, zero-variance
0, short-series 0), the matrix builder (known ±1 pairs, the >=10-bar
eligibility filter, sector-grouped ordering, constant-price 0.0, the
fewer-than-two-eligible empty shape, exact 1.0 diagonal), and the
GET /api/market/correlation endpoint (minutes default/clamp/400).

Bar histories are injected with minute-aligned timestamps; every ticker gets
one extra tick in a trailing forming minute so aggregate_minute_bars keeps
all seeded closes as COMPLETED bars.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market import PriceCache
from app.market.universe import US_UNIVERSE
from app.routes.market import (
    DEFAULT_CORRELATION_MINUTES,
    MAX_CORRELATION_MINUTES,
    MIN_CORRELATION_MINUTES,
    _pearson,
    compute_correlation_matrix,
    create_market_router,
)

# Minute-aligned base timestamp (1_200_000 % 60 == 0).
BASE = 1_200_000

# 12 ascending closes — 12 completed one-minute bars (>= the 10-bar gate).
UP_CLOSES = [100.0 + i for i in range(12)]


def seed_closes(
    cache: PriceCache, ticker: str, closes: list[float], start_minute: int = 0
) -> None:
    """One tick per minute at each close, plus a forming-minute tick so every
    seeded close aggregates to a completed bar. All tickers end at the same
    minute when ``start_minute + len(closes)`` matches, keeping the global
    window anchor aligned."""
    for i, close in enumerate(closes):
        cache.update(ticker, close, timestamp=BASE + (start_minute + i) * 60)
    cache.update(
        ticker, closes[-1], timestamp=BASE + (start_minute + len(closes)) * 60
    )


class TestPearson:
    def test_perfect_positive(self):
        assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert _pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_orthogonal_is_zero(self):
        xs = [1.0, -1.0, 1.0, -1.0]
        ys = [1.0, 1.0, -1.0, -1.0]
        assert _pearson(xs, ys) == pytest.approx(0.0)

    def test_zero_variance_is_zero(self):
        assert _pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) == 0.0
        assert _pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) == 0.0

    def test_short_series_is_zero(self):
        assert _pearson([], []) == 0.0
        assert _pearson([1.0], [2.0]) == 0.0


class TestCorrelationMatrix:
    def test_scaled_pair_correlates_plus_one(self):
        # B = 2 x A: identical log returns -> r exactly 1.0.
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "BBB", [2 * c for c in UP_CLOSES])
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert result["tickers"] == ["AAA", "BBB"]
        assert result["matrix"][0][1] == pytest.approx(1.0)
        assert result["matrix"][1][0] == pytest.approx(1.0)

    def test_inverse_pair_correlates_minus_one(self):
        # B = 10000 / A: log returns negated -> r ~= -1.0 (cent rounding).
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "BBB", [10000.0 / c for c in UP_CLOSES])
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert result["matrix"][0][1] <= -0.99

    def test_diagonal_is_exactly_one(self):
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "BBB", [2 * c for c in UP_CLOSES])
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        for i in range(len(result["tickers"])):
            assert result["matrix"][i][i] == 1.0

    def test_constant_price_correlates_zero(self):
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "FLAT", [100.0] * 12)
        seed_closes(cache, "BBB", [2 * c for c in UP_CLOSES])
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        i = result["tickers"].index("FLAT")
        j = result["tickers"].index("AAA")
        assert result["matrix"][i][j] == 0.0
        assert result["matrix"][i][i] == 1.0  # self stays pinned at 1.0

    def test_tickers_below_ten_bars_are_excluded(self):
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "BBB", [2 * c for c in UP_CLOSES])
        # 5 closes ending at the same minute as the others -> only 5 bars.
        seed_closes(cache, "FEW", [50.0 + i for i in range(5)], start_minute=7)
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert "FEW" not in result["tickers"]
        assert result["tickers"] == ["AAA", "BBB"]

    def test_sector_grouped_ordering(self):
        # US sectors: JPM financials, AAPL/MSFT tech -> financials block
        # first, tickers alphabetical inside each block.
        cache = PriceCache()
        for ticker in ("MSFT", "AAPL", "JPM"):
            seed_closes(cache, ticker, UP_CLOSES)
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert result["tickers"] == ["JPM", "AAPL", "MSFT"]
        assert result["sectors"] == {
            "JPM": "financials",
            "AAPL": "tech",
            "MSFT": "tech",
        }

    def test_fewer_than_two_eligible_returns_empty(self):
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)  # one eligible ticker only
        result = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert result == {"tickers": [], "sectors": {}, "matrix": [], "minutes": 30}

    def test_empty_cache_returns_empty(self):
        result = compute_correlation_matrix(PriceCache(), US_UNIVERSE, 45)
        assert result == {"tickers": [], "sectors": {}, "matrix": [], "minutes": 45}

    def test_window_excludes_older_bars(self):
        # The same 12-bar history is eligible at minutes=30 (all 12 bars in
        # window) but NOT at minutes=5 — only the newest 5 bars fall inside
        # the window, under the 10-bar gate, so the window boundary really
        # trims old bars rather than counting the whole ring buffer.
        cache = PriceCache()
        seed_closes(cache, "AAA", UP_CLOSES)
        seed_closes(cache, "BBB", [2 * c for c in UP_CLOSES])
        narrow = compute_correlation_matrix(cache, US_UNIVERSE, 5)
        assert narrow["tickers"] == []
        wide = compute_correlation_matrix(cache, US_UNIVERSE, 30)
        assert wide["tickers"] == ["AAA", "BBB"]


@pytest_asyncio.fixture
async def correlation_client():
    cache = PriceCache()
    seed_closes(cache, "AAPL", UP_CLOSES)
    seed_closes(cache, "MSFT", [2 * c for c in UP_CLOSES])
    app = FastAPI()
    app.include_router(create_market_router(cache))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
class TestCorrelationEndpoint:
    async def test_response_shape_and_default_minutes(self, correlation_client):
        resp = await correlation_client.get("/api/market/correlation")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"tickers", "sectors", "matrix", "minutes"}
        assert body["minutes"] == DEFAULT_CORRELATION_MINUTES
        n = len(body["tickers"])
        assert n == 2
        assert len(body["matrix"]) == n
        assert all(len(row) == n for row in body["matrix"])
        assert body["matrix"][0][0] == 1.0

    async def test_minutes_clamped_low_and_high(self, correlation_client):
        low = await correlation_client.get("/api/market/correlation?minutes=1")
        assert low.status_code == 200
        assert low.json()["minutes"] == MIN_CORRELATION_MINUTES

        high = await correlation_client.get("/api/market/correlation?minutes=999")
        assert high.status_code == 200
        assert high.json()["minutes"] == MAX_CORRELATION_MINUTES

    async def test_non_integer_minutes_is_400(self, correlation_client):
        resp = await correlation_client.get("/api/market/correlation?minutes=abc")
        assert resp.status_code == 400
        assert "error" in resp.json()
