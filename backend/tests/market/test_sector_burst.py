"""Tests for the sector map and sector-correlated event bursts (M3.2b).

``compute_peer_shocks`` is deterministic given an injected rng, so the burst
decision and the 25-50% shock band are forced (never sampled blindly). The
step() integration tests use dt=0.0 — the GBM drift/diffusion terms vanish
and prices move ONLY via the scripted event + burst, making every expected
price exact.
"""

from __future__ import annotations

import random

from app.market import PriceCache
from app.market.seed_prices import SECTORS, SEED_PRICES, sector_for
from app.market.simulator import (
    BURST_FRACTION_MAX,
    BURST_FRACTION_MIN,
    BURST_PROBABILITY,
    GBMSimulator,
    compute_peer_shocks,
)


class ScriptedRng:
    """random.Random stand-in with scripted draws for deterministic bursts.

    ``random()`` pops from a script (1.0 — never fire — once exhausted);
    ``uniform(a, b)`` returns the midpoint; ``choice`` returns a fixed value.
    """

    def __init__(self, random_values: list[float], choice_value: int = 1) -> None:
        self._random_values = list(random_values)
        self._choice_value = choice_value

    def random(self) -> float:
        return self._random_values.pop(0) if self._random_values else 1.0

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2

    def choice(self, seq):
        return self._choice_value


class TestSectorMap:
    """Static ticker -> sector map (M3.2b/M3.4)."""

    def test_known_sectors(self):
        for ticker in ("AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX", "TSLA"):
            assert sector_for(ticker) == "tech"
        for ticker in ("JPM", "V"):
            assert sector_for(ticker) == "financials"
        for ticker in ("BTC", "ETH"):
            assert sector_for(ticker) == "crypto"

    def test_unknown_ticker_is_other(self):
        assert sector_for("ZZZZ") == "other"

    def test_input_normalized(self):
        assert sector_for("  aapl ") == "tech"

    def test_every_seeded_ticker_has_a_sector(self):
        assert set(SECTORS) == set(SEED_PRICES)


class TestComputePeerShocks:
    """Burst decision + per-peer shock math, forced via injected rng."""

    CANDIDATES = ["NVDA", "AAPL", "MSFT", "JPM", "V", "BTC", "ZZZZ"]

    def test_forced_burst_shocks_all_same_sector_peers(self):
        # random()=0.0 < 0.35 forces the burst; uniform -> midpoint 0.375.
        rng = ScriptedRng([0.0])
        shocks = compute_peer_shocks("NVDA", 0.04, -1, self.CANDIDATES, rng=rng)

        # Every tech peer, never the source ticker or other sectors/unknowns.
        assert set(shocks) == {"AAPL", "MSFT"}
        expected = -1 * 0.04 * (BURST_FRACTION_MIN + BURST_FRACTION_MAX) / 2
        for shock in shocks.values():
            assert shock == expected  # same sign, 37.5% of the source move

    def test_forced_no_burst_returns_empty(self):
        rng = ScriptedRng([BURST_PROBABILITY])  # >= threshold — no burst
        assert compute_peer_shocks("NVDA", 0.04, 1, self.CANDIDATES, rng=rng) == {}

    def test_other_sector_never_bursts(self):
        rng = ScriptedRng([0.0])  # would fire if consulted
        candidates = ["ZZZZ", "YYYY", "AAPL"]
        assert compute_peer_shocks("ZZZZ", 0.04, 1, candidates, rng=rng) == {}

    def test_no_peers_returns_empty(self):
        rng = ScriptedRng([0.0])
        assert compute_peer_shocks("JPM", 0.04, 1, ["JPM", "AAPL", "BTC"], rng=rng) == {}

    def test_shock_band_and_fire_rate_with_real_rng(self):
        """Real jitter: fires ~35% of the time, shocks always in the 25-50% band."""
        rng = random.Random(42)
        magnitude, sign = 0.04, -1
        fired = 0
        for _ in range(500):
            shocks = compute_peer_shocks(
                "AAPL", magnitude, sign, ["AAPL", "MSFT", "GOOGL", "JPM"], rng=rng
            )
            if not shocks:
                continue
            fired += 1
            assert set(shocks) == {"MSFT", "GOOGL"}
            for shock in shocks.values():
                assert shock < 0  # same sign as the source move
                fraction = abs(shock) / magnitude
                assert BURST_FRACTION_MIN <= fraction <= BURST_FRACTION_MAX
        assert 120 <= fired <= 230  # ~0.35 * 500 = 175


class TestSimulatorBurstIntegration:
    """step() applies staged peer shocks in the same tick (dt=0 — exact math)."""

    TICKERS = ["NVDA", "AAPL", "JPM", "BTC"]

    def _forced_burst_step(self) -> dict[str, float]:
        # Draw order: NVDA event check (0.0 -> fires), burst decision
        # (0.0 -> fires); AAPL/JPM/BTC event checks fall back to 1.0 (no fire).
        # uniform midpoints: magnitude 0.035, peer fraction 0.375; sign -1.
        rng = ScriptedRng([0.0, 0.0], choice_value=-1)
        sim = GBMSimulator(tickers=list(self.TICKERS), dt=0.0, rng=rng)
        return sim.step()

    def test_forced_event_bursts_peers_and_spares_non_peers(self):
        result = self._forced_burst_step()

        # Source: NVDA takes the full -3.5% shock.
        assert result["NVDA"] == round(SEED_PRICES["NVDA"] * (1 - 0.035), 2)
        # Same-sector peer: 37.5% of the source magnitude, same sign.
        assert result["AAPL"] == round(SEED_PRICES["AAPL"] * (1 - 0.035 * 0.375), 2)
        # Non-peers (financials / crypto) untouched — dt=0 means no GBM drift.
        assert result["JPM"] == SEED_PRICES["JPM"]
        assert result["BTC"] == SEED_PRICES["BTC"]

    def test_burst_cascades_through_cache_funnel_as_events(self):
        """Peer shocks >= 1% record their own MarketEvents via the cache."""
        cache = PriceCache()
        for ticker in self.TICKERS:
            cache.update(ticker, SEED_PRICES[ticker], timestamp=1000.0)  # flat seed

        for ticker, price in self._forced_burst_step().items():
            cache.update(ticker, price, timestamp=1001.0)

        events = {e.ticker: e for e in cache.get_events()}
        # NVDA -3.5% and its AAPL peer shock -1.3125% both cross the 1%
        # event threshold; the untouched tickers record nothing.
        assert set(events) == {"NVDA", "AAPL"}
        assert events["NVDA"].direction == "down"
        assert events["AAPL"].direction == "down"
        assert events["AAPL"].change_percent == -1.31

    def test_no_burst_without_event(self):
        rng = ScriptedRng([])  # every draw is 1.0 — no events at all
        sim = GBMSimulator(tickers=list(self.TICKERS), dt=0.0, rng=rng)
        result = sim.step()
        assert result == {t: SEED_PRICES[t] for t in self.TICKERS}
