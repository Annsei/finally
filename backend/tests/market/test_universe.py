"""Tests for the MarketUniverse abstraction and US_UNIVERSE equivalence (CN-1)."""

from app.market.seed_prices import (
    CRYPTO_TICKERS,
    DEFAULT_PARAMS,
    DEFAULT_WATCHLIST,
    SECTORS,
    SEED_PRICES,
    TICKER_PARAMS,
    asset_class_for,
    sector_for,
)
from app.market.simulator import GBMSimulator
from app.market.universe import US_UNIVERSE, MarketUniverse


class TestUSUniverseConstruction:
    """US_UNIVERSE wraps the existing seed_prices.py constants verbatim."""

    def test_wraps_existing_constant_objects(self):
        """Same objects, no copies — behavior can never drift from the constants."""
        assert US_UNIVERSE.seed_prices is SEED_PRICES
        assert US_UNIVERSE.ticker_params is TICKER_PARAMS
        assert US_UNIVERSE.default_params is DEFAULT_PARAMS
        assert US_UNIVERSE.default_watchlist is DEFAULT_WATCHLIST
        assert US_UNIVERSE.sectors is SECTORS

    def test_names_empty_for_us(self):
        assert US_UNIVERSE.names == {}

    def test_crypto_tickers_are_btc_eth(self):
        assert US_UNIVERSE.crypto_tickers == frozenset(CRYPTO_TICKERS)


class TestUSUniverseCorrelation:
    """pairwise_correlation reproduces GBMSimulator._pairwise_correlation exactly."""

    def test_matches_simulator_for_every_pair(self):
        """Every ordered pair over seeds + unknowns gives the identical rho."""
        tickers = list(SEED_PRICES) + ["PYPL", "ZZZZ"]
        for t1 in tickers:
            for t2 in tickers:
                assert US_UNIVERSE.pairwise_correlation(
                    t1, t2
                ) == GBMSimulator._pairwise_correlation(t1, t2), (t1, t2)

    def test_tsla_special_case(self):
        """TSLA does its own thing — 0.3 with everything, both argument orders."""
        assert US_UNIVERSE.pairwise_correlation("TSLA", "NVDA") == 0.3
        assert US_UNIVERSE.pairwise_correlation("NVDA", "TSLA") == 0.3
        assert US_UNIVERSE.pairwise_correlation("TSLA", "JPM") == 0.3

    def test_group_and_cross_values(self):
        assert US_UNIVERSE.pairwise_correlation("AAPL", "GOOGL") == 0.6
        assert US_UNIVERSE.pairwise_correlation("JPM", "V") == 0.5
        assert US_UNIVERSE.pairwise_correlation("BTC", "ETH") == 0.7
        assert US_UNIVERSE.pairwise_correlation("AAPL", "JPM") == 0.3
        assert US_UNIVERSE.pairwise_correlation("ZZZZ", "AAPL") == 0.3


class TestUSUniverseHelpers:
    """sector_for / asset_class_for mirror the module-level helpers."""

    def test_sector_for_matches_module_helper(self):
        for ticker in list(SEED_PRICES) + ["PYPL", " aapl ", "zzz"]:
            assert US_UNIVERSE.sector_for(ticker) == sector_for(ticker)

    def test_asset_class_for_matches_module_helper(self):
        for ticker in list(SEED_PRICES) + ["PYPL", " btc ", "eth"]:
            assert US_UNIVERSE.asset_class_for(ticker) == asset_class_for(ticker)

    def test_unknown_ticker_defaults(self):
        assert US_UNIVERSE.sector_for("UNKNOWN") == "other"
        assert US_UNIVERSE.asset_class_for("UNKNOWN") == "equity"


class TestMarketUniverseGeneric:
    """The dataclass itself behaves for arbitrary (non-US) universes."""

    def _make(self) -> MarketUniverse:
        return MarketUniverse(
            seed_prices={"AAA": 10.0, "BBB": 20.0, "CCC": 30.0},
            ticker_params={"AAA": {"sigma": 0.2, "mu": 0.05}},
            default_params={"sigma": 0.25, "mu": 0.05},
            default_watchlist=["AAA", "BBB"],
            sectors={"AAA": "one", "BBB": "one"},
            names={"AAA": "Alpha"},
            crypto_tickers=frozenset(),
            correlation_groups={"one": frozenset({"AAA", "BBB"})},
            group_correlations={"one": 0.8},
            cross_group_corr=0.2,
        )

    def test_intra_group_and_cross(self):
        uni = self._make()
        assert uni.pairwise_correlation("AAA", "BBB") == 0.8
        assert uni.pairwise_correlation("AAA", "CCC") == 0.2
        assert uni.pairwise_correlation("XXX", "YYY") == 0.2

    def test_independent_tickers_beat_group_membership(self):
        """An independent ticker uses independent_corr even inside its group."""
        uni = MarketUniverse(
            seed_prices={"AAA": 10.0, "BBB": 20.0},
            ticker_params={},
            default_params={"sigma": 0.25, "mu": 0.05},
            default_watchlist=["AAA", "BBB"],
            sectors={},
            names={},
            crypto_tickers=frozenset(),
            correlation_groups={"one": frozenset({"AAA", "BBB"})},
            group_correlations={"one": 0.8},
            cross_group_corr=0.2,
            independent_tickers=frozenset({"AAA"}),
            independent_corr=0.1,
        )
        assert uni.pairwise_correlation("AAA", "BBB") == 0.1
        assert uni.pairwise_correlation("BBB", "AAA") == 0.1
