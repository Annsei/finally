"""Simulator/factory universe injection (CN-1): seeds, params, correlations,
and the closed-session asset-class check come from the injected universe."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import numpy as np
import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.seed_prices import DEFAULT_WATCHLIST, SEED_PRICES
from app.market.seed_prices_cn import CN_DEFAULT_PARAMS, CN_SEED_PRICES, CN_UNIVERSE
from app.market.session import SessionClock
from app.market.simulator import GBMSimulator, SimulatorDataSource
from app.market.universe import US_UNIVERSE
from tests.conftest import FakeTime


def make_closed_clock() -> tuple[SessionClock, FakeTime]:
    """A session clock already driven into the CLOSED state."""
    fake = FakeTime()
    clock = SessionClock(30.0, 10.0, now=fake)
    fake.advance(30.0)
    assert clock.tick() == ["close"]
    return clock, fake


class TestGBMSimulatorUniverse:
    """GBMSimulator(universe=...) sources seeds/params/correlations from it."""

    def test_cn_seed_prices(self):
        sim = GBMSimulator(list(CN_SEED_PRICES), universe=CN_UNIVERSE)
        assert sim.get_price("600519") == CN_SEED_PRICES["600519"]  # ~¥1700
        assert sim.get_price("601988") == CN_SEED_PRICES["601988"]

    def test_cn_ticker_params(self):
        sim = GBMSimulator(["600519"], universe=CN_UNIVERSE)
        assert sim._params["600519"] == {"sigma": 0.22, "mu": 0.06}

    def test_unknown_cn_ticker_gets_random_seed_and_default_params(self):
        sim = GBMSimulator(["999999"], universe=CN_UNIVERSE)
        assert 50.0 <= sim.get_price("999999") <= 300.0
        assert sim._params["999999"] == CN_DEFAULT_PARAMS

    def test_cn_step_returns_all_tickers(self):
        sim = GBMSimulator(list(CN_SEED_PRICES), universe=CN_UNIVERSE)
        result = sim.step()
        assert set(result.keys()) == set(CN_SEED_PRICES)

    def test_cholesky_matches_universe_correlations(self):
        """L @ L.T reconstructs exactly the universe's pairwise matrix."""
        tickers = ["600519", "000858", "300750", "601318", "600900"]
        sim = GBMSimulator(tickers, universe=CN_UNIVERSE)
        corr = sim._cholesky @ sim._cholesky.T
        for i, t1 in enumerate(tickers):
            for j, t2 in enumerate(tickers):
                expected = 1.0 if i == j else CN_UNIVERSE.pairwise_correlation(t1, t2)
                assert corr[i, j] == pytest.approx(expected), (t1, t2)

    def test_us_universe_injection_matches_no_universe(self):
        """Injecting US_UNIVERSE is behavior-identical to the constant path."""
        sim_none = GBMSimulator(list(DEFAULT_WATCHLIST))
        sim_us = GBMSimulator(list(DEFAULT_WATCHLIST), universe=US_UNIVERSE)
        for ticker in DEFAULT_WATCHLIST:
            assert sim_none.get_price(ticker) == sim_us.get_price(ticker)
            assert sim_none._params[ticker] == sim_us._params[ticker]
        assert np.array_equal(sim_none._cholesky, sim_us._cholesky)


class TestSimulatorSourceUniverse:
    """SimulatorDataSource(universe=...) — CN has no crypto, so a closed
    session freezes the whole market; open sessions tick from CN seeds."""

    async def test_cn_everything_frozen_while_closed(self):
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock, universe=CN_UNIVERSE
        )
        await source.start(["600519", "000858"])
        try:
            version_after_seed = cache.version
            await asyncio.sleep(0.1)
            # Empty crypto set -> not a single cache write while closed.
            assert cache.version == version_after_seed
        finally:
            await source.stop()

    async def test_cn_ticks_from_cn_seeds_while_open(self):
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, universe=CN_UNIVERSE
        )
        await source.start(["600519"])
        try:
            seeded = cache.get("600519")
            assert seeded.price == CN_SEED_PRICES["600519"]
            await asyncio.sleep(0.1)
            assert cache.get("600519").timestamp > seeded.timestamp
        finally:
            await source.stop()

    async def test_us_universe_keeps_crypto_ticking_while_closed(self):
        """Parity with the module-constant path: BTC ticks, AAPL freezes."""
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock, universe=US_UNIVERSE
        )
        await source.start(["AAPL", "BTC"])
        try:
            aapl_before = cache.get("AAPL")
            btc_before = cache.get("BTC")
            await asyncio.sleep(0.1)
            assert cache.get("AAPL") is aapl_before
            assert cache.get("BTC").timestamp > btc_before.timestamp
        finally:
            await source.stop()

    async def test_no_universe_uses_us_seed_prices(self):
        """Default path unchanged: seeds still come from SEED_PRICES."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        try:
            assert cache.get("AAPL").price == SEED_PRICES["AAPL"]
        finally:
            await source.stop()


class TestFactoryUniversePassthrough:
    """create_market_data_source forwards the universe to the simulator."""

    def test_simulator_receives_universe(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache, None, CN_UNIVERSE)
        assert isinstance(source, SimulatorDataSource)
        assert source._universe is CN_UNIVERSE

    def test_default_universe_is_none(self):
        cache = PriceCache()
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)
        assert source._universe is None
