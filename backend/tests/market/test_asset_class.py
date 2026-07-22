"""Tests for crypto asset support (M3.3): seeds, classification, serialization."""

from __future__ import annotations

from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.market.seed_prices import (
    CRYPTO_TICKERS,
    DEFAULT_WATCHLIST,
    SEED_PRICES,
    TICKER_PARAMS,
    asset_class_for,
)


class TestAssetClassFor:
    """'crypto' for the crypto set, 'equity' for everything else."""

    def test_crypto_tickers(self):
        assert asset_class_for("BTC") == "crypto"
        assert asset_class_for("ETH") == "crypto"

    def test_equity_tickers(self):
        for ticker in DEFAULT_WATCHLIST:
            assert asset_class_for(ticker) == "equity"

    def test_unknown_tickers_default_to_equity(self):
        assert asset_class_for("PYPL") == "equity"
        assert asset_class_for("ZZZZ") == "equity"

    def test_input_is_normalized(self):
        assert asset_class_for(" btc ") == "crypto"
        assert asset_class_for("eth") == "crypto"
        assert asset_class_for(" aapl ") == "equity"


class TestCryptoSeeds:
    """BTC/ETH seeded with realistic prices and ~3x equity volatility."""

    def test_seed_prices(self):
        assert SEED_PRICES["BTC"] == 65000.00
        assert SEED_PRICES["ETH"] == 3500.00

    def test_crypto_volatility_is_high(self):
        equity_sigmas = [
            TICKER_PARAMS[t]["sigma"] for t in DEFAULT_WATCHLIST
        ]
        typical_equity = sum(equity_sigmas) / len(equity_sigmas)
        for ticker in CRYPTO_TICKERS:
            assert TICKER_PARAMS[ticker]["sigma"] >= 2.5 * typical_equity
            assert TICKER_PARAMS[ticker]["mu"] > 0

    def test_crypto_not_in_default_watchlist(self):
        """The default watchlist stays the 10 equities (PLAN.md §7)."""
        assert len(DEFAULT_WATCHLIST) == 10
        assert "BTC" not in DEFAULT_WATCHLIST
        assert "ETH" not in DEFAULT_WATCHLIST
        for ticker in DEFAULT_WATCHLIST:
            assert ticker in SEED_PRICES  # equities keep their seeds


class TestPriceUpdateAssetClass:
    """PriceUpdate.to_dict() always carries asset_class."""

    def test_crypto_to_dict(self):
        update = PriceUpdate(ticker="BTC", price=65000.0, previous_price=64900.0)
        assert update.asset_class == "crypto"
        assert update.to_dict()["asset_class"] == "crypto"

    def test_equity_to_dict(self):
        update = PriceUpdate(ticker="AAPL", price=190.5, previous_price=190.0)
        assert update.to_dict()["asset_class"] == "equity"

    def test_unknown_ticker_to_dict_is_equity(self):
        update = PriceUpdate(ticker="PYPL", price=60.0, previous_price=60.0)
        assert update.to_dict()["asset_class"] == "equity"

    def test_cache_funnel_carries_asset_class(self):
        """Updates written through the PriceCache serialize with asset_class."""
        cache = PriceCache()
        assert cache.update("BTC", 65000.0).to_dict()["asset_class"] == "crypto"
        assert cache.update("ETH", 3500.0).to_dict()["asset_class"] == "crypto"
        assert cache.update("AAPL", 190.0).to_dict()["asset_class"] == "equity"
