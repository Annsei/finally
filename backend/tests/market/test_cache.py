"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        """Test price update with upward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12


class TestPriceCacheSessionFields:
    """Session-state carry semantics: prev_close, day_high, day_low."""

    def test_first_update_initializes_session_fields(self):
        """First price seen becomes prev_close and both extremes."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.00)
        assert update.prev_close == 190.00
        assert update.day_high == 190.00
        assert update.day_low == 190.00

    def test_prev_close_stays_constant_across_updates(self):
        """prev_close is captured once and never moves with the price."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 195.00)
        update = cache.update("AAPL", 185.00)
        assert update.prev_close == 190.00

    def test_day_extremes_track_running_high_low(self):
        """day_high/day_low expand to cover every price seen."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 195.00)
        update = cache.update("AAPL", 185.00)
        assert update.day_high == 195.00
        assert update.day_low == 185.00

    def test_day_change_fields_derive_from_prev_close(self):
        """day_change/day_change_percent compare price vs session prev_close."""
        cache = PriceCache()
        cache.update("AAPL", 200.00)
        update = cache.update("AAPL", 210.00)
        assert update.day_change == 10.00
        assert update.day_change_percent == 5.0

    def test_explicit_prev_close_wins_and_is_carried(self):
        """An explicit prev_close (Massive prevDay.c) overrides and persists."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, prev_close=180.00)
        update = cache.update("AAPL", 191.00)  # No explicit value this time
        assert update.prev_close == 180.00

    def test_explicit_day_extremes_used_verbatim(self):
        """Explicit day_high/day_low (Massive day.h/day.l) are used as given."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, day_high=193.10, day_low=187.40)
        assert update.day_high == 193.10
        assert update.day_low == 187.40

    def test_session_fields_reset_after_remove(self):
        """Removing a ticker clears its session state; re-add starts fresh."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        update = cache.update("AAPL", 210.00)
        assert update.prev_close == 210.00
        assert update.day_high == 210.00
        assert update.day_low == 210.00

    def test_session_fields_rounded_to_2_decimals(self):
        """Explicit session inputs are rounded like prices."""
        cache = PriceCache()
        update = cache.update(
            "AAPL", 190.12345, prev_close=180.98765, day_high=195.55555, day_low=170.11111
        )
        assert update.prev_close == 180.99
        assert update.day_high == 195.56
        assert update.day_low == 170.11


class TestPriceCacheQuoteFields:
    """Volume and bid/ask flow through the update() funnel."""

    def test_defaults_when_source_supplies_nothing(self):
        """No quote kwargs → bid=ask=price and volume 0.0 (FakeMarketSource path)."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.volume == 0.0
        assert update.bid == 190.50
        assert update.ask == 190.50

    def test_explicit_quote_fields_pass_through(self):
        """Explicit volume/bid/ask land on the PriceUpdate."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, volume=8000.0, bid=190.45, ask=190.55)
        assert update.volume == 8000.0
        assert update.bid == 190.45
        assert update.ask == 190.55

    def test_bid_ask_rounded_to_2_decimals(self):
        """Bid/ask are rounded like prices."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, bid=190.44444, ask=190.55555)
        assert update.bid == 190.44
        assert update.ask == 190.56

    def test_negative_volume_clamped_to_zero(self):
        """Volume is clamped >= 0 in the funnel."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, volume=-100.0)
        assert update.volume == 0.0

    def test_to_dict_emits_all_new_keys(self):
        """Serialized updates always carry volume/bid/ask."""
        cache = PriceCache()
        result = cache.update("AAPL", 190.50).to_dict()
        for key in ("volume", "bid", "ask"):
            assert key in result


class TestPriceCacheHistoryBuffer:
    """Per-ticker 1-second OHLCV ring buffer fed from update()."""

    def test_unknown_ticker_returns_empty(self):
        cache = PriceCache()
        assert cache.get_history("NOPE") == []

    def test_single_update_creates_bar(self):
        """First tick opens a bar with open=high=low=close=price."""
        cache = PriceCache()
        cache.update("AAPL", 190.50, timestamp=1000.4, volume=100.0)
        bars = cache.get_history("AAPL")
        assert bars == [
            {
                "time": 1000,
                "open": 190.50,
                "high": 190.50,
                "low": 190.50,
                "close": 190.50,
                "volume": 100.0,
            }
        ]

    def test_same_second_updates_merge(self):
        """Ticks in the same Unix second merge: OHLCV math is exact."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1000.1, volume=100.0)
        cache.update("AAPL", 192.00, timestamp=1000.5, volume=50.0)
        cache.update("AAPL", 189.00, timestamp=1000.9, volume=25.0)
        bars = cache.get_history("AAPL")
        assert len(bars) == 1
        bar = bars[0]
        assert bar["time"] == 1000
        assert bar["open"] == 190.00
        assert bar["high"] == 192.00
        assert bar["low"] == 189.00
        assert bar["close"] == 189.00
        assert bar["volume"] == 175.0

    def test_new_second_appends_bar(self):
        """A tick in a new second appends a fresh bar."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1000.5, volume=100.0)
        cache.update("AAPL", 191.00, timestamp=1001.2, volume=200.0)
        bars = cache.get_history("AAPL")
        assert [b["time"] for b in bars] == [1000, 1001]
        assert bars[1] == {
            "time": 1001,
            "open": 191.00,
            "high": 191.00,
            "low": 191.00,
            "close": 191.00,
            "volume": 200.0,
        }

    def test_older_bucket_ignored(self):
        """Updates whose bucket precedes the newest bar are ignored (buffer only)."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1005.0, volume=100.0)
        cache.update("AAPL", 999.00, timestamp=1004.0, volume=999.0)
        bars = cache.get_history("AAPL")
        assert len(bars) == 1
        assert bars[0]["time"] == 1005
        assert bars[0]["volume"] == 100.0
        # The latest-price record still updated even though the bar was ignored
        assert cache.get_price("AAPL") == 999.00

    def test_eviction_at_capacity(self):
        """Oldest bars are evicted once capacity is exceeded."""
        cache = PriceCache(history_capacity=5)
        for i in range(8):
            cache.update("AAPL", 190.00 + i, timestamp=float(1000 + i))
        bars = cache.get_history("AAPL")
        assert len(bars) == 5
        assert [b["time"] for b in bars] == [1003, 1004, 1005, 1006, 1007]

    def test_limit_returns_most_recent_ascending(self):
        """limit slices the N most-recent bars, still ascending by time."""
        cache = PriceCache()
        for i in range(5):
            cache.update("AAPL", 190.00 + i, timestamp=float(1000 + i))
        bars = cache.get_history("AAPL", limit=2)
        assert [b["time"] for b in bars] == [1003, 1004]

    def test_remove_clears_history(self):
        """Removing a ticker clears its ring buffer."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1000.0)
        cache.remove("AAPL")
        assert cache.get_history("AAPL") == []

    def test_history_returns_copies(self):
        """Mutating returned bars does not affect the buffer."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1000.0)
        bars = cache.get_history("AAPL")
        bars[0]["close"] = 0.0
        assert cache.get_history("AAPL")[0]["close"] == 190.00

    def test_default_volume_accumulates_as_zero(self):
        """Sources that pass no volume still form bars (volume stays 0.0)."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, timestamp=1000.1)
        cache.update("AAPL", 191.00, timestamp=1000.6)
        assert cache.get_history("AAPL")[0]["volume"] == 0.0
