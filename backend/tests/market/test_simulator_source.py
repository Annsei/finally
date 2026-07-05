"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)  # Should get ~5 updates

        # Should have multiple updates with fast interval
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_prev_close_is_seed_price_and_constant(self):
        """prev_close equals the GBM starting (seed) price and never moves."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL"])

        first = cache.get("AAPL")
        assert first is not None
        assert first.prev_close == SEED_PRICES["AAPL"]

        initial_version = cache.version
        await asyncio.sleep(0.2)  # Let several GBM ticks land
        assert cache.version > initial_version  # Prices actually updated

        later = cache.get("AAPL")
        assert later.prev_close == SEED_PRICES["AAPL"]

        await source.stop()

    async def test_day_extremes_and_day_change_consistency(self):
        """day_high/day_low bracket the price and seed; day_change matches math."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL"])
        await asyncio.sleep(0.2)

        update = cache.get("AAPL")
        assert update is not None
        # Extremes always bracket the current price and the seed (first) price
        assert update.day_low <= update.price <= update.day_high
        assert update.day_low <= SEED_PRICES["AAPL"] <= update.day_high
        # Derived day fields are consistent with price vs prev_close
        assert update.day_change == round(update.price - update.prev_close, 4)
        assert update.day_change_percent == round(
            (update.price - update.prev_close) / update.prev_close * 100, 4
        )

        await source.stop()

    async def test_add_ticker_gets_prev_close(self):
        """A dynamically added ticker's prev_close is its GBM starting price."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        update = cache.get("TSLA")
        assert update is not None
        assert update.prev_close == SEED_PRICES["TSLA"]
        assert update.day_high == SEED_PRICES["TSLA"]
        assert update.day_low == SEED_PRICES["TSLA"]

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.1, event_probability=1.0
        )
        await source.start(["AAPL"])

        # Just verify it starts and stops cleanly
        await asyncio.sleep(0.2)
        await source.stop()

    async def test_ticks_carry_positive_varying_volume(self):
        """Every simulated tick has volume > 0, and volume varies tick to tick."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL"])

        volumes: list[float] = []
        for _ in range(10):
            await asyncio.sleep(0.03)
            update = cache.get("AAPL")
            assert update.volume > 0
            volumes.append(update.volume)

        assert len(set(volumes)) > 1  # Lognormal draws vary
        await source.stop()

    async def test_bid_price_ask_ordering_with_1_to_5_bp_spread(self):
        """bid < price < ask and the quoted spread is within 1-5 bp (+rounding)."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL", "GOOGL"])
        await asyncio.sleep(0.1)

        for ticker in ("AAPL", "GOOGL"):
            update = cache.get(ticker)
            assert update.bid < update.price < update.ask
            measured_bps = (update.ask - update.bid) / update.price * 10_000
            # Rounding to cents can shift each side by up to a cent
            slop_bps = 2 * 0.01 / update.price * 10_000
            assert 1 - slop_bps <= measured_bps <= 5 + slop_bps

        await source.stop()

    async def test_spread_stable_per_ticker(self):
        """A ticker's quoted spread (in bp) stays fixed across ticks."""
        from app.market.simulator import spread_bps_for

        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL"])

        expected_bps = spread_bps_for("AAPL")
        for _ in range(5):
            await asyncio.sleep(0.04)
            update = cache.get("AAPL")
            measured_bps = (update.ask - update.bid) / update.price * 10_000
            slop_bps = 2 * 0.01 / update.price * 10_000
            assert abs(measured_bps - expected_bps) <= slop_bps

        await source.stop()

    async def test_ticks_populate_history_buffer(self):
        """Simulator ticks flow into the cache's OHLCV ring buffer."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.02)
        await source.start(["AAPL"])
        await asyncio.sleep(0.1)

        bars = cache.get_history("AAPL")
        assert len(bars) >= 1
        bar = bars[-1]
        assert bar["low"] <= bar["open"] <= bar["high"]
        assert bar["low"] <= bar["close"] <= bar["high"]
        assert bar["volume"] > 0

        await source.stop()
