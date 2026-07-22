"""Sector-burst sector_fn injection for the CN universe (CN-2 §6).

The module-level ``sector_for`` classifies CN numeric codes as "other" (they
are not in the US map), so bursts would never fire for A-shares. Injecting the
active universe's ``sector_for`` makes 白酒/新能源 peers cascade correctly.
"""

from __future__ import annotations

from app.market.profiles import CN_PROFILE
from app.market.seed_prices_cn import CN_UNIVERSE
from app.market.simulator import (
    BURST_FRACTION_MAX,
    BURST_FRACTION_MIN,
    compute_peer_shocks,
)

# Two 白酒 names + one 新能源 name; the burst must stay inside 白酒.
CANDIDATES = ["600519", "000858", "300750", "601988"]


class ScriptedRng:
    def __init__(self, random_values):
        self._values = list(random_values)

    def random(self):
        return self._values.pop(0) if self._values else 1.0

    def uniform(self, a, b):
        return (a + b) / 2


class TestCnSectorFnInjection:
    def test_without_sector_fn_cn_codes_never_burst(self):
        # Default (US) sector_for returns "other" for 600519 -> no peers.
        rng = ScriptedRng([0.0])  # would fire if a sector existed
        assert compute_peer_shocks("600519", 0.04, 1, CANDIDATES, rng=rng) == {}

    def test_with_sector_fn_baijiu_peers_cascade(self):
        rng = ScriptedRng([0.0])  # force the burst
        shocks = compute_peer_shocks(
            "600519", 0.04, 1, CANDIDATES, rng=rng, sector_fn=CN_UNIVERSE.sector_for
        )
        # Only the OTHER 白酒 name (000858) is shocked — same sector, not self.
        assert set(shocks) == {"000858"}
        expected = 1 * 0.04 * (BURST_FRACTION_MIN + BURST_FRACTION_MAX) / 2
        assert shocks["000858"] == expected

    def test_cross_sector_not_shocked(self):
        rng = ScriptedRng([0.0])
        shocks = compute_peer_shocks(
            "300750", 0.04, 1, CANDIDATES, rng=rng, sector_fn=CN_UNIVERSE.sector_for
        )
        # 300750 (新能源) has no 新能源 peer in CANDIDATES -> empty (no cross-burst).
        assert shocks == {}

    def test_profile_universe_sector_fn_matches(self):
        # The profile's universe is what main.py injects into the simulator.
        assert CN_PROFILE.universe.sector_for("600519") == "白酒"
        assert CN_PROFILE.universe.sector_for("000858") == "白酒"
        assert CN_PROFILE.universe.sector_for("300750") == "新能源"
