"""Tests for MassiveDataSource (mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource


def _make_snapshot(
    ticker: str,
    price: float,
    timestamp_ms: int,
    prev_close: float | None = None,
    day_high: float | None = None,
    day_low: float | None = None,
    day_volume: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
) -> MagicMock:
    """Create a mock Massive snapshot object.

    prev_day/day/last_quote default to None (absent in the API response) so
    tests exercise the fallback paths unless values are provided explicitly.
    """
    snap = MagicMock()
    snap.ticker = ticker
    snap.last_trade = MagicMock()
    snap.last_trade.price = price
    snap.last_trade.timestamp = timestamp_ms
    snap.prev_day = None
    snap.day = None
    snap.last_quote = None
    if prev_close is not None:
        snap.prev_day = MagicMock()
        snap.prev_day.close = prev_close
    if day_high is not None or day_low is not None or day_volume is not None:
        snap.day = MagicMock()
        snap.day.high = day_high
        snap.day.low = day_low
        snap.day.volume = day_volume
    if bid is not None or ask is not None:
        snap.last_quote = MagicMock()
        snap.last_quote.bid_price = bid
        snap.last_quote.ask_price = ask
    return snap


@pytest.mark.asyncio
class TestMassiveDataSource:
    """Unit tests for MassiveDataSource with mocked API."""

    async def test_poll_updates_cache(self):
        """Test that polling updates the cache."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,  # Long interval so the loop doesn't auto-poll
        )
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [
            _make_snapshot("AAPL", 190.50, 1707580800000),
            _make_snapshot("GOOGL", 175.25, 1707580800000),
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_malformed_snapshot_skipped(self):
        """Test that malformed snapshots are skipped gracefully."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        good_snap = _make_snapshot("AAPL", 190.50, 1707580800000)
        bad_snap = MagicMock()
        bad_snap.ticker = "BAD"
        bad_snap.last_trade = None  # Will cause AttributeError

        with patch.object(source, "_fetch_snapshots", return_value=[good_snap, bad_snap]):
            await source._poll_once()

        # Good ticker processed, bad one skipped
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_api_error_does_not_crash(self):
        """Test that API errors don't crash the poller."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None  # No update happened

    async def test_timestamp_conversion(self):
        """Test that timestamps are converted from milliseconds to seconds."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800000)]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.timestamp == 1707580800.0  # Converted to seconds

    async def test_add_ticker(self):
        """Test adding a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_uppercase_normalization(self):
        """Test that tickers are normalized to uppercase."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("aapl")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_strips_whitespace(self):
        """Test that ticker whitespace is stripped."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("  AAPL  ")
        assert "AAPL" in source.get_tickers()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]

        tickers = source.get_tickers()
        assert tickers == ["AAPL", "GOOGL"]

    async def test_empty_tickers_skips_poll(self):
        """Test that polling is skipped when there are no tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = []

        # Should not call _fetch_snapshots
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_stop_is_idempotent(self):
        """Test that stop() can be called multiple times."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task(self):
        """Test that stop() cancels the polling task."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=10.0)

        # Mock the client and start
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        # Verify task is running
        assert source._task is not None
        assert not source._task.done()

        # Stop and verify task is cancelled
        await source.stop()
        assert source._task is None

    async def test_start_immediate_poll(self):
        """Test that start() does an immediate poll before starting the loop."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        mock_snapshots = [_make_snapshot("AAPL", 190.50, 1707580800000)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
                await source.start(["AAPL"])

        # Cache should have data immediately from the first poll
        assert cache.get_price("AAPL") == 190.50

        await source.stop()

    async def test_prev_day_close_mapped(self):
        """Snapshot prevDay.c becomes prev_close; day fields derive from it."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = _make_snapshot("AAPL", 190.50, 1707580800000, prev_close=188.25)
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.prev_close == 188.25
        assert update.day_change == round(190.50 - 188.25, 4)
        assert update.day_change_percent == round((190.50 - 188.25) / 188.25 * 100, 4)

    async def test_day_high_low_mapped(self):
        """Snapshot day.h/day.l become day_high/day_low."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = _make_snapshot(
            "AAPL", 190.50, 1707580800000, day_high=193.10, day_low=187.40
        )
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.day_high == 193.10
        assert update.day_low == 187.40

    async def test_fallback_first_price_when_snapshot_fields_absent(self):
        """Without prevDay/day, prev_close falls back to the first price seen
        (and stays constant) while extremes track running high/low."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        first_poll = [_make_snapshot("AAPL", 190.50, 1707580800000)]
        with patch.object(source, "_fetch_snapshots", return_value=first_poll):
            await source._poll_once()

        first = cache.get("AAPL")
        assert first.prev_close == 190.50  # Fallback: first price seen
        assert first.day_high == 190.50
        assert first.day_low == 190.50

        second_poll = [_make_snapshot("AAPL", 192.00, 1707580815000)]
        with patch.object(source, "_fetch_snapshots", return_value=second_poll):
            await source._poll_once()

        second = cache.get("AAPL")
        assert second.prev_close == 190.50  # Fallback stays constant
        assert second.day_high == 192.00  # Running extremes advance
        assert second.day_low == 190.50

    async def test_zero_prev_day_close_falls_back(self):
        """A zero-filled prevDay.c (off-hours) is ignored in favor of fallback."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        # Simulates a zero-filled prevDay aggregate (prevDay.c == 0.0)
        snap = _make_snapshot("AAPL", 190.50, 1707580800000, prev_close=0.0)

        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.prev_close == 190.50  # Fell back to first price seen

    async def test_added_ticker_gets_prev_close_on_first_poll(self):
        """A ticker added via add_ticker() gets a prev_close once polled."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._client = MagicMock()

        await source.add_ticker("NVDA")
        snap = _make_snapshot("NVDA", 805.00, 1707580800000)
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("NVDA")
        assert update is not None
        assert update.prev_close == 805.00


@pytest.mark.asyncio
class TestMassiveQuoteAndVolume:
    """Batch 2: lastQuote bid/ask mapping and day-volume deltas."""

    @staticmethod
    def _make_source() -> tuple[PriceCache, MassiveDataSource]:
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()
        return cache, source

    @staticmethod
    async def _poll(source: MassiveDataSource, snapshots: list) -> None:
        with patch.object(source, "_fetch_snapshots", return_value=snapshots):
            await source._poll_once()

    async def test_last_quote_bid_ask_mapped(self):
        """lastQuote.p / lastQuote.P become bid / ask."""
        cache, source = self._make_source()
        snap = _make_snapshot("AAPL", 190.50, 1707580800000, bid=190.45, ask=190.55)
        await self._poll(source, [snap])

        update = cache.get("AAPL")
        assert update.bid == 190.45
        assert update.ask == 190.55

    async def test_absent_quote_falls_back_to_price(self):
        """No lastQuote → bid = ask = price (zero spread)."""
        cache, source = self._make_source()
        snap = _make_snapshot("AAPL", 190.50, 1707580800000)
        await self._poll(source, [snap])

        update = cache.get("AAPL")
        assert update.bid == 190.50
        assert update.ask == 190.50

    async def test_non_positive_quote_falls_back_to_price(self):
        """Zero/negative quote sides are ignored in favor of the price."""
        cache, source = self._make_source()
        snap = _make_snapshot("AAPL", 190.50, 1707580800000, bid=0.0, ask=-1.0)
        await self._poll(source, [snap])

        update = cache.get("AAPL")
        assert update.bid == 190.50
        assert update.ask == 190.50

    async def test_day_volume_delta_across_two_polls(self):
        """Volume is the delta of cumulative day.v between consecutive polls."""
        cache, source = self._make_source()
        await self._poll(
            source, [_make_snapshot("AAPL", 190.00, 1707580800000, day_volume=10_000.0)]
        )
        # First poll: no previous cumulative to diff against
        assert cache.get("AAPL").volume == 0.0

        await self._poll(
            source, [_make_snapshot("AAPL", 190.50, 1707580815000, day_volume=12_500.0)]
        )
        assert cache.get("AAPL").volume == 2_500.0

    async def test_day_volume_decrease_clamped_to_zero(self):
        """A cumulative decrease (session reset) clamps the delta at 0."""
        cache, source = self._make_source()
        await self._poll(
            source, [_make_snapshot("AAPL", 190.00, 1707580800000, day_volume=10_000.0)]
        )
        await self._poll(
            source, [_make_snapshot("AAPL", 190.50, 1707580815000, day_volume=4_000.0)]
        )
        assert cache.get("AAPL").volume == 0.0

        # The lower cumulative becomes the new baseline
        await self._poll(
            source, [_make_snapshot("AAPL", 191.00, 1707580830000, day_volume=4_300.0)]
        )
        assert cache.get("AAPL").volume == 300.0

    async def test_absent_day_volume_yields_zero(self):
        """Snapshots without day.v report volume 0.0 (and bid=ask=price)."""
        cache, source = self._make_source()
        snap = _make_snapshot("AAPL", 190.50, 1707580800000)
        await self._poll(source, [snap])

        update = cache.get("AAPL")
        assert update.volume == 0.0
        assert update.bid == update.ask == 190.50

    async def test_remove_ticker_resets_volume_baseline(self):
        """Removing a ticker clears its cumulative-volume baseline."""
        cache, source = self._make_source()
        await self._poll(
            source, [_make_snapshot("AAPL", 190.00, 1707580800000, day_volume=10_000.0)]
        )
        await source.remove_ticker("AAPL")

        source._tickers = ["AAPL"]
        await self._poll(
            source, [_make_snapshot("AAPL", 190.50, 1707580815000, day_volume=12_000.0)]
        )
        # Treated as a first poll again — no delta
        assert cache.get("AAPL").volume == 0.0
