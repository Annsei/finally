"""Market sentiment index tests (P4 §1).

Covers the three-axis math (breadth advancing share with exact-flat tickers
excluded from the numerator, day-amplitude mapping with the 2%-reads-100
clamp and the prev_close<=0 skip, the 10-minute volume flow ratio's three
anchor points and its empty-prior neutral), the sample<2 neutral gate, the
weighted score + five-tier label, and the GET /api/market/sentiment endpoint
shape.

All cache updates inject timestamps at a fixed minute-aligned BASE so the
volume axis (anchored on the newest COMPLETED minute bar) is deterministic.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market import PriceCache
from app.market.sentiment import (
    compute_market_sentiment,
    label_for_score,
    sentiment_context_line,
)
from app.routes.market import create_market_router

# Minute-aligned base timestamp (1_200_000 % 60 == 0).
BASE = 1_200_000


def seed_quote(
    cache: PriceCache,
    ticker: str,
    price: float,
    prev_close: float,
    day_high: float | None = None,
    day_low: float | None = None,
) -> None:
    """One deterministic quote (single tick at BASE — no completed bars)."""
    cache.update(
        ticker,
        price,
        timestamp=BASE,
        prev_close=prev_close,
        day_high=day_high if day_high is not None else max(price, prev_close),
        day_low=day_low if day_low is not None else min(price, prev_close),
    )


def seed_volume_history(
    cache: PriceCache,
    ticker: str,
    prior_total: float,
    recent_total: float,
    price: float = 100.0,
) -> None:
    """21 one-tick minutes: prior window (10 bars), recent window (10 bars),
    plus a forming minute that aggregate_minute_bars drops."""
    for m in range(21):
        if m < 10:
            volume = prior_total / 10.0
        elif m < 20:
            volume = recent_total / 10.0
        else:
            volume = 0.0
        cache.update(ticker, price, timestamp=BASE + m * 60, volume=volume)


class TestBreadthAxis:
    def test_all_advancing_reads_100(self):
        cache = PriceCache()
        for i in range(4):
            seed_quote(cache, f"T{i}", 110.0, 100.0)
        assert compute_market_sentiment(cache)["axes"]["breadth"] == 100.0

    def test_all_declining_reads_0(self):
        cache = PriceCache()
        for i in range(4):
            seed_quote(cache, f"T{i}", 90.0, 100.0)
        assert compute_market_sentiment(cache)["axes"]["breadth"] == 0.0

    def test_exact_flat_counts_denominator_not_numerator(self):
        # 2 advancers + 2 exactly-flat out of 4 -> 50, not 100.
        cache = PriceCache()
        seed_quote(cache, "UP1", 110.0, 100.0)
        seed_quote(cache, "UP2", 110.0, 100.0)
        seed_quote(cache, "FLAT1", 100.0, 100.0)
        seed_quote(cache, "FLAT2", 100.0, 100.0)
        assert compute_market_sentiment(cache)["axes"]["breadth"] == 50.0


class TestVolatilityAxis:
    def test_one_percent_amplitude_reads_50(self):
        cache = PriceCache()
        seed_quote(cache, "A", 100.0, 100.0, day_high=100.5, day_low=99.5)
        seed_quote(cache, "B", 100.0, 100.0, day_high=100.5, day_low=99.5)
        assert compute_market_sentiment(cache)["axes"]["volatility"] == 50.0

    def test_two_percent_amplitude_reads_100(self):
        cache = PriceCache()
        seed_quote(cache, "A", 100.0, 100.0, day_high=101.0, day_low=99.0)
        seed_quote(cache, "B", 100.0, 100.0, day_high=101.0, day_low=99.0)
        assert compute_market_sentiment(cache)["axes"]["volatility"] == 100.0

    def test_above_two_percent_clamps_at_100(self):
        cache = PriceCache()
        seed_quote(cache, "A", 100.0, 100.0, day_high=104.0, day_low=96.0)
        seed_quote(cache, "B", 100.0, 100.0, day_high=104.0, day_low=96.0)
        assert compute_market_sentiment(cache)["axes"]["volatility"] == 100.0

    def test_zero_amplitude_reads_0(self):
        cache = PriceCache()
        seed_quote(cache, "A", 100.0, 100.0, day_high=100.0, day_low=100.0)
        seed_quote(cache, "B", 100.0, 100.0, day_high=100.0, day_low=100.0)
        assert compute_market_sentiment(cache)["axes"]["volatility"] == 0.0

    def test_non_positive_prev_close_is_skipped(self):
        # The zero-prev_close ticker must not drag the mean toward zero.
        cache = PriceCache()
        seed_quote(cache, "A", 100.0, 100.0, day_high=101.0, day_low=99.0)
        seed_quote(cache, "B", 100.0, 100.0, day_high=101.0, day_low=99.0)
        seed_quote(cache, "ZERO", 100.0, 0.0, day_high=104.0, day_low=96.0)
        assert compute_market_sentiment(cache)["axes"]["volatility"] == 100.0


class TestVolumeAxis:
    @pytest.mark.parametrize(
        ("prior", "recent", "expected"),
        [
            (100.0, 100.0, 50.0),  # ratio 1.0 -> 50
            (100.0, 300.0, 100.0),  # ratio >= 2.0 -> 100
            (100.0, 20.0, 0.0),  # ratio <= 0.5 -> 0
            (100.0, 150.0, 75.0),  # ratio 1.5 -> 75 (upper linear leg)
            (100.0, 75.0, 25.0),  # ratio 0.75 -> 25 (lower linear leg)
        ],
    )
    def test_ratio_mapping(self, prior, recent, expected):
        cache = PriceCache()
        seed_volume_history(cache, "A", prior / 2, recent / 2)
        seed_volume_history(cache, "B", prior / 2, recent / 2)
        assert compute_market_sentiment(cache)["axes"]["volume"] == expected

    def test_empty_prior_segment_is_neutral(self):
        cache = PriceCache()
        seed_volume_history(cache, "A", 0.0, 100.0)
        seed_volume_history(cache, "B", 0.0, 100.0)
        assert compute_market_sentiment(cache)["axes"]["volume"] == 50.0

    def test_no_completed_bars_is_neutral(self):
        # Single tick per ticker -> only a forming minute -> neutral 50.
        cache = PriceCache()
        seed_quote(cache, "A", 110.0, 100.0)
        seed_quote(cache, "B", 110.0, 100.0)
        assert compute_market_sentiment(cache)["axes"]["volume"] == 50.0


class TestScoreAndSampleGate:
    def test_empty_cache_is_neutral(self):
        result = compute_market_sentiment(PriceCache())
        assert result == {
            "score": 50,
            "label": "neutral",
            "axes": {"breadth": 50.0, "volatility": 50.0, "volume": 50.0},
            "sample_size": 0,
        }

    def test_single_ticker_is_neutral(self):
        cache = PriceCache()
        seed_quote(cache, "ONLY", 200.0, 100.0, day_high=210.0, day_low=90.0)
        result = compute_market_sentiment(cache)
        assert result["sample_size"] == 1
        assert result["score"] == 50
        assert result["label"] == "neutral"
        assert result["axes"] == {"breadth": 50.0, "volatility": 50.0, "volume": 50.0}

    def test_score_is_weighted_round_of_axes(self):
        # breadth 100 (all up), volatility 100 (2% amplitude), volume 100
        # (ratio >= 2) -> score 100, label hot.
        cache = PriceCache()
        for ticker in ("A", "B"):
            for m in range(21):
                volume = 5.0 if m < 10 else (25.0 if m < 20 else 0.0)
                cache.update(
                    ticker,
                    110.0,
                    timestamp=BASE + m * 60,
                    prev_close=100.0,
                    day_high=101.0,
                    day_low=99.0,
                    volume=volume,
                )
        result = compute_market_sentiment(cache)
        assert result["axes"] == {"breadth": 100.0, "volatility": 100.0, "volume": 100.0}
        assert result["score"] == 100
        assert result["label"] == "hot"

    def test_score_mixes_axes_with_half_quarter_quarter_weights(self):
        # breadth 100, volatility 0, volume 50 -> 0.5*100 + 0.25*0 + 0.25*50
        # = 62.5 -> round -> 62 -> active.
        cache = PriceCache()
        seed_quote(cache, "A", 110.0, 100.0, day_high=110.0, day_low=110.0)
        seed_quote(cache, "B", 110.0, 100.0, day_high=110.0, day_low=110.0)
        result = compute_market_sentiment(cache)
        assert result["axes"] == {"breadth": 100.0, "volatility": 0.0, "volume": 50.0}
        assert result["score"] == 62
        assert result["label"] == "active"


class TestLabelTiers:
    @pytest.mark.parametrize(
        ("score", "label"),
        [
            (0, "frozen"),
            (19, "frozen"),
            (20, "cool"),
            (39, "cool"),
            (40, "neutral"),
            (59, "neutral"),
            (60, "active"),
            (79, "active"),
            (80, "hot"),
            (100, "hot"),
        ],
    )
    def test_thresholds(self, score, label):
        assert label_for_score(score) == label


class TestSentimentContextLine:
    def test_below_sample_gate_returns_none(self):
        sentiment = compute_market_sentiment(PriceCache())
        assert sentiment_context_line(sentiment) is None
        assert sentiment_context_line(sentiment, zh=True) is None

    def test_english_line_format(self):
        cache = PriceCache()
        seed_quote(cache, "A", 110.0, 100.0, day_high=110.0, day_low=110.0)
        seed_quote(cache, "B", 110.0, 100.0, day_high=110.0, day_low=110.0)
        line = sentiment_context_line(compute_market_sentiment(cache))
        assert line == (
            "Market sentiment: 62/100 (active) — breadth 100, volatility 0, volume 50"
        )

    def test_chinese_line_format(self):
        cache = PriceCache()
        seed_quote(cache, "A", 110.0, 100.0, day_high=110.0, day_low=110.0)
        seed_quote(cache, "B", 110.0, 100.0, day_high=110.0, day_low=110.0)
        line = sentiment_context_line(compute_market_sentiment(cache), zh=True)
        assert line == "市场情绪：62/100（活跃）—— 涨跌家数 100，波动 0，量能 50"


@pytest_asyncio.fixture
async def sentiment_client():
    """Market router over a deterministic two-ticker cache."""
    cache = PriceCache()
    seed_quote(cache, "AAPL", 110.0, 100.0, day_high=101.0, day_low=99.0)
    seed_quote(cache, "MSFT", 90.0, 100.0, day_high=101.0, day_low=99.0)
    app = FastAPI()
    app.include_router(create_market_router(cache))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
class TestSentimentEndpoint:
    async def test_response_shape(self, sentiment_client):
        resp = await sentiment_client.get("/api/market/sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"score", "label", "axes", "sample_size"}
        assert set(body["axes"]) == {"breadth", "volatility", "volume"}
        assert isinstance(body["score"], int)
        assert body["label"] in ("frozen", "cool", "neutral", "active", "hot")
        assert body["sample_size"] == 2
        # 1 advancer of 2 -> breadth 50; 2% amplitude -> volatility 100.
        assert body["axes"]["breadth"] == 50.0
        assert body["axes"]["volatility"] == 100.0

    async def test_empty_cache_reads_neutral(self):
        app = FastAPI()
        app.include_router(create_market_router(PriceCache()))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/market/sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == 50
        assert body["label"] == "neutral"
        assert body["sample_size"] == 0
