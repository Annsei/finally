"""Tests for GBMSimulator."""

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import (
    MAX_SPREAD_BPS,
    MIN_SPREAD_BPS,
    GBMSimulator,
    compute_quote,
    draw_volume,
    spread_bps_for,
)


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim.get_tickers()) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if '.' in price_str:
            decimal_part = price_str.split('.')[1]
            assert len(decimal_part) <= 2


class TestSpread:
    """Deterministic per-ticker bid/ask spread (1-5 bp)."""

    def test_spread_within_1_to_5_bps(self):
        """Every ticker's configured spread lands in [1, 5] basis points."""
        for ticker in list(SEED_PRICES) + ["PYPL", "ZZZZ", "X"]:
            bps = spread_bps_for(ticker)
            assert MIN_SPREAD_BPS <= bps <= MAX_SPREAD_BPS

    def test_spread_deterministic_per_ticker(self):
        """Same ticker always yields the same spread."""
        for ticker in SEED_PRICES:
            assert spread_bps_for(ticker) == spread_bps_for(ticker)

    def test_quote_brackets_price(self):
        """bid < price < ask for realistic prices."""
        for ticker, price in SEED_PRICES.items():
            bid, ask = compute_quote(ticker, price)
            assert bid < price < ask

    def test_quote_brackets_price_at_one_dollar(self):
        """Even at $1 the rounding guard keeps bid < price < ask."""
        bid, ask = compute_quote("AAPL", 1.00)
        assert bid < 1.00 < ask
        assert bid > 0

    def test_quote_spread_matches_configured_bps(self):
        """Measured spread equals the ticker's spread, within 2dp rounding slop."""
        for ticker, price in SEED_PRICES.items():
            bid, ask = compute_quote(ticker, price)
            measured_bps = (ask - bid) / price * 10_000
            expected_bps = spread_bps_for(ticker)
            # Each side can move up to half a cent from rounding (or a full
            # cent from the minimum-tick guard).
            slop_bps = 2 * 0.01 / price * 10_000
            assert abs(measured_bps - expected_bps) <= slop_bps

    def test_quote_rounded_to_two_decimals(self):
        bid, ask = compute_quote("AAPL", 190.123456)
        assert round(bid, 2) == bid
        assert round(ask, 2) == ask

    def test_quote_stable_for_same_price(self):
        """Fixed spread: same ticker + price → identical quote every time."""
        assert compute_quote("AAPL", 190.50) == compute_quote("AAPL", 190.50)


class TestDrawVolume:
    """Per-tick lognormal volume."""

    def test_volume_always_positive(self):
        for _ in range(1000):
            assert draw_volume() > 0

    def test_volume_varies_tick_to_tick(self):
        draws = {draw_volume() for _ in range(50)}
        assert len(draws) > 1

    def test_volume_typical_range(self):
        """Median-ish draws land roughly in the 1k-100k band."""
        draws = sorted(draw_volume() for _ in range(500))
        # Middle 80% of draws should sit inside the target band
        p10, p90 = draws[50], draws[449]
        assert p10 >= 1_000
        assert p90 <= 100_000
