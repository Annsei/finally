"""Frozen-market behavior (M3.1/M3.3): equities freeze while closed, crypto ticks 24/7."""

from __future__ import annotations

import asyncio

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.session import SessionClock
from app.market.simulator import GBMSimulator, SimulatorDataSource
from tests.conftest import FakeTime


def make_closed_clock() -> tuple[SessionClock, FakeTime]:
    """A session clock already driven into the CLOSED state."""
    fake = FakeTime()
    clock = SessionClock(30.0, 10.0, now=fake)
    fake.advance(30.0)
    assert clock.tick() == ["close"]
    return clock, fake


class TestGBMStepOnly:
    """GBMSimulator.step(only=...) advances a subset, freezing the rest."""

    def test_only_subset_advances(self):
        sim = GBMSimulator(["AAPL", "BTC"])
        aapl_before = sim.get_price("AAPL")
        result = sim.step(only={"BTC"})
        assert set(result.keys()) == {"BTC"}
        assert sim.get_price("AAPL") == aapl_before  # frozen

    def test_only_none_advances_all(self):
        sim = GBMSimulator(["AAPL", "BTC"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "BTC"}

    def test_only_empty_set_freezes_everything(self):
        sim = GBMSimulator(["AAPL", "BTC"])
        prices_before = {t: sim.get_price(t) for t in ("AAPL", "BTC")}
        assert sim.step(only=set()) == {}
        assert {t: sim.get_price(t) for t in ("AAPL", "BTC")} == prices_before


class TestSimulatorSourceSessionFreeze:
    """SimulatorDataSource skips equity cache writes while the session is closed."""

    async def test_equity_frozen_crypto_ticks_while_closed(self):
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock
        )
        await source.start(["AAPL", "BTC"])
        try:
            aapl_before = cache.get("AAPL")
            btc_before = cache.get("BTC")
            await asyncio.sleep(0.1)
            # Equity: identical record — not a single cache write while closed.
            assert cache.get("AAPL") is aapl_before
            # Crypto: kept ticking 24/7.
            assert cache.get("BTC").timestamp > btc_before.timestamp
        finally:
            await source.stop()

    async def test_cache_version_unchanged_for_equity_only_market(self):
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock
        )
        await source.start(["AAPL", "MSFT"])
        try:
            version_after_seed = cache.version
            await asyncio.sleep(0.1)
            assert cache.version == version_after_seed
        finally:
            await source.stop()

    async def test_equity_resumes_ticking_after_reopen(self):
        clock, fake = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock
        )
        await source.start(["AAPL"])
        try:
            frozen = cache.get("AAPL")
            await asyncio.sleep(0.05)
            assert cache.get("AAPL") is frozen
            fake.advance(10.0)
            assert clock.tick() == ["open"]
            await asyncio.sleep(0.1)
            assert cache.get("AAPL").timestamp > frozen.timestamp
        finally:
            await source.stop()

    async def test_no_clock_means_everything_ticks(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])
        try:
            before = cache.get("AAPL")
            await asyncio.sleep(0.1)
            assert cache.get("AAPL").timestamp > before.timestamp
        finally:
            await source.stop()

    async def test_crypto_added_while_closed_seeds_and_ticks(self):
        """Adding BTC via the watchlist while closed streams immediately (M3.3)."""
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock
        )
        await source.start(["AAPL"])
        try:
            await source.add_ticker("BTC")
            seeded = cache.get("BTC")
            assert seeded is not None
            assert seeded.price == SEED_PRICES["BTC"]
            assert seeded.to_dict()["asset_class"] == "crypto"
            await asyncio.sleep(0.1)
            assert cache.get("BTC").timestamp > seeded.timestamp
        finally:
            await source.stop()

    async def test_equity_added_while_closed_seeds_but_freezes(self):
        """A just-watched equity gets a quotable seed price, then freezes."""
        clock, _ = make_closed_clock()
        cache = PriceCache()
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, session_clock=clock
        )
        await source.start(["AAPL"])
        try:
            await source.add_ticker("PYPL")
            seeded = cache.get("PYPL")
            assert seeded is not None
            await asyncio.sleep(0.05)
            assert cache.get("PYPL") is seeded  # frozen after the seed write
        finally:
            await source.stop()
