"""Tests for PriceUpdate dataclass."""

import pytest

from app.market.models import PriceUpdate


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test price change calculation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative price change."""
        update = PriceUpdate(ticker="AAPL", price=189.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test percentage change calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=100.00, timestamp=1234567890.0)
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test percentage change calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=200.00, timestamp=1234567890.0)
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=0.00, timestamp=1234567890.0)
        assert update.change_percent == 0.0

    def test_direction_up(self):
        """Test direction calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=191.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "up"

    def test_direction_down(self):
        """Test direction calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=189.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "down"

    def test_direction_flat(self):
        """Test direction calculation (flat)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "flat"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["direction"] == "up"

    def test_session_fields_default_to_price(self):
        """Omitted session fields normalize to the current price (first tick)."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.prev_close == 190.50
        assert update.day_high == 190.50
        assert update.day_low == 190.50
        assert update.day_change == 0.0
        assert update.day_change_percent == 0.0

    def test_explicit_session_fields(self):
        """Explicit prev_close/day_high/day_low are used verbatim."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            timestamp=1234567890.0,
            prev_close=180.00,
            day_high=195.00,
            day_low=179.50,
        )
        assert update.prev_close == 180.00
        assert update.day_high == 195.00
        assert update.day_low == 179.50
        assert update.day_change == 10.50
        assert update.day_change_percent == round(10.50 / 180.00 * 100, 4)

    def test_day_change_negative(self):
        """day_change/day_change_percent go negative below prev_close."""
        update = PriceUpdate(
            ticker="AAPL",
            price=175.00,
            previous_price=176.00,
            timestamp=1234567890.0,
            prev_close=180.00,
        )
        assert update.day_change == -5.00
        assert update.day_change_percent == round(-5.00 / 180.00 * 100, 4)

    def test_day_change_percent_guard_nonpositive_prev_close(self):
        """prev_close <= 0 yields day_change_percent of 0.0 (no division)."""
        zero = PriceUpdate(ticker="X", price=100.0, previous_price=100.0, prev_close=0.0)
        negative = PriceUpdate(ticker="X", price=100.0, previous_price=100.0, prev_close=-1.0)
        assert zero.day_change_percent == 0.0
        assert negative.day_change_percent == 0.0

    def test_to_dict_includes_day_fields(self):
        """to_dict() always carries the five session fields."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            timestamp=1234567890.0,
            prev_close=180.00,
            day_high=195.00,
            day_low=179.50,
        )
        result = update.to_dict()

        assert result["prev_close"] == 180.00
        assert result["day_change"] == 10.50
        assert result["day_change_percent"] == round(10.50 / 180.00 * 100, 4)
        assert result["day_high"] == 195.00
        assert result["day_low"] == 179.50

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error
