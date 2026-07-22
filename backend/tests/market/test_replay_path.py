"""Intraday path + volume synthesis unit tests (D3 contract §1/§5).

Pins the pure functions the replay source is built on:

- ``build_day_path``: O→L→H→C (bullish) / O→H→L→C (bearish) piecewise-linear
  skeleton, seeded micro-noise clamped inside [low, high], EXACTLY-once
  touches of high and low, last point EXACTLY the close (the settlement
  machinery depends on it), deterministic per seed, and documented
  degradations for tiny n_points / zero-amplitude bars.
- ``build_day_volumes``: even distribution with seeded ±30% jitter,
  conservation of the (rounded) daily total, non-negativity.
- ``replay_seed``: stable across calls (CRC32, not seed-salted hash()).
"""

from __future__ import annotations

import random

import pytest

from app.market.replay_source import (
    NOISE_FRACTION,
    build_day_path,
    build_day_volumes,
    replay_seed,
)

BULL_BAR = {"open": 100.0, "high": 104.0, "low": 97.0, "close": 102.0, "volume": 50_000}
BEAR_BAR = {"open": 102.0, "high": 104.0, "low": 97.0, "close": 98.0, "volume": 50_000}


def rng_for(ticker: str = "AAPL", date: str = "2026-06-01") -> random.Random:
    return random.Random(replay_seed(ticker, date))


class ZeroNoiseRng:
    """Fake rng whose uniform() is always 0 — path == pure skeleton."""

    def uniform(self, a: float, b: float) -> float:  # noqa: ARG002
        return 0.0


class TestReplaySeed:
    def test_stable_across_calls(self):
        assert replay_seed("AAPL", "2026-06-01") == replay_seed("AAPL", "2026-06-01")

    def test_varies_by_ticker_and_date(self):
        seeds = {
            replay_seed("AAPL", "2026-06-01"),
            replay_seed("MSFT", "2026-06-01"),
            replay_seed("AAPL", "2026-06-02"),
        }
        assert len(seeds) == 3


class TestBuildDayPathShape:
    def test_length_matches_n_points(self):
        assert len(build_day_path(BULL_BAR, 50, rng_for())) == 50

    def test_deterministic_same_seed_same_path(self):
        p1 = build_day_path(BULL_BAR, 40, rng_for())
        p2 = build_day_path(BULL_BAR, 40, rng_for())
        assert p1 == p2

    def test_different_seed_different_path(self):
        p1 = build_day_path(BULL_BAR, 40, rng_for("AAPL", "2026-06-01"))
        p2 = build_day_path(BULL_BAR, 40, rng_for("AAPL", "2026-06-02"))
        assert p1 != p2

    @pytest.mark.parametrize("bar", [BULL_BAR, BEAR_BAR])
    def test_last_point_exactly_close(self, bar):
        path = build_day_path(bar, 30, rng_for())
        assert path[-1] == bar["close"]

    def test_deep_range_bar_last_point_exactly_close(self):
        # >2x final-segment ratio: naive t=1.0 interpolation lands 1 ulp off
        # (verify finding); anchors must be stamped with their exact values.
        bar = {
            "open": 232.24290462572844,
            "high": 247.49907809036355,
            "low": 69.64425543367331,
            "close": 199.30727308645064,
        }
        path = build_day_path(bar, 216, rng_for())
        assert path[-1] == bar["close"]
        assert max(path) == bar["high"]
        assert min(path) == bar["low"]

    @pytest.mark.parametrize("bar", [BULL_BAR, BEAR_BAR])
    def test_all_points_within_low_high(self, bar):
        path = build_day_path(bar, 60, rng_for())
        assert all(bar["low"] <= p <= bar["high"] for p in path)

    def test_first_point_is_open(self):
        path = build_day_path(BULL_BAR, 30, rng_for())
        assert path[0] == BULL_BAR["open"]

    @pytest.mark.parametrize("bar", [BULL_BAR, BEAR_BAR])
    def test_touches_high_and_low_exactly_once(self, bar):
        path = build_day_path(bar, 60, rng_for())
        assert path.count(bar["high"]) == 1
        assert path.count(bar["low"]) == 1

    def test_bullish_skeleton_low_before_high(self):
        path = build_day_path(BULL_BAR, 60, rng_for())
        assert path.index(BULL_BAR["low"]) < path.index(BULL_BAR["high"])

    def test_bearish_skeleton_high_before_low(self):
        path = build_day_path(BEAR_BAR, 60, rng_for())
        assert path.index(BEAR_BAR["high"]) < path.index(BEAR_BAR["low"])

    def test_zero_noise_rng_reproduces_pure_skeleton_anchors(self):
        """With zero noise the path is piecewise linear between the anchors."""
        path = build_day_path(BULL_BAR, 61, ZeroNoiseRng())
        assert path[0] == 100.0
        assert path[-1] == 102.0
        # Anchor slots: round(j*(n-1)/3) for j in 0..3 => 0, 20, 40, 60.
        assert path[20] == 97.0
        assert path[40] == 104.0
        # Midpoint of the O->L leg is exactly linear.
        assert path[10] == pytest.approx((100.0 + 97.0) / 2)

    def test_noise_bounded_by_fraction_of_skeleton(self):
        """Noisy points deviate from the pure skeleton by <= 0.1% of price
        (the clamp only ever pulls points back toward the skeleton)."""
        skeleton = build_day_path(BULL_BAR, 61, ZeroNoiseRng())
        noisy = build_day_path(BULL_BAR, 61, rng_for())
        for base, point in zip(skeleton, noisy):
            assert abs(point - base) <= NOISE_FRACTION * base + 1e-12


class TestBuildDayPathEdges:
    def test_zero_points_returns_empty(self):
        assert build_day_path(BULL_BAR, 0, rng_for()) == []

    def test_negative_points_returns_empty(self):
        assert build_day_path(BULL_BAR, -3, rng_for()) == []

    def test_one_point_is_close(self):
        assert build_day_path(BULL_BAR, 1, rng_for()) == [BULL_BAR["close"]]

    def test_two_points_ends_at_close_within_range(self):
        """Documented degradation: anchors drop front-first, so a generic
        bar keeps [second_extreme, close]."""
        path = build_day_path(BULL_BAR, 2, rng_for())
        assert len(path) == 2
        assert path[-1] == BULL_BAR["close"]
        assert path == [BULL_BAR["high"], BULL_BAR["close"]]
        bear = build_day_path(BEAR_BAR, 2, rng_for())
        assert bear == [BEAR_BAR["low"], BEAR_BAR["close"]]

    def test_three_points_touch_both_extremes_and_end_at_close(self):
        path = build_day_path(BULL_BAR, 3, rng_for())
        assert path == [BULL_BAR["low"], BULL_BAR["high"], BULL_BAR["close"]]
        bear = build_day_path(BEAR_BAR, 3, rng_for())
        assert bear == [BEAR_BAR["high"], BEAR_BAR["low"], BEAR_BAR["close"]]

    def test_zero_amplitude_bar_constant_path(self):
        flat = {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "volume": 0}
        path = build_day_path(flat, 10, rng_for())
        assert path == [50.0] * 10

    def test_close_at_high_touches_high_exactly_once_at_the_end(self):
        bar = {"open": 100.0, "high": 103.0, "low": 99.0, "close": 103.0, "volume": 1}
        path = build_day_path(bar, 40, rng_for())
        assert path[-1] == 103.0
        assert path.count(103.0) == 1  # the close IS the single high touch
        assert path.count(99.0) == 1

    def test_open_at_low_touches_low_exactly_once_at_the_start(self):
        bar = {"open": 99.0, "high": 103.0, "low": 99.0, "close": 101.0, "volume": 1}
        path = build_day_path(bar, 40, rng_for())
        assert path[0] == 99.0
        assert path.count(99.0) == 1  # the open IS the single low touch
        assert path.count(103.0) == 1

    def test_inconsistent_extremes_are_repaired(self):
        """A bar whose high/low fail to bracket open/close never crashes and
        the path stays within the repaired bracket."""
        bad = {"open": 100.0, "high": 99.0, "low": 101.0, "close": 102.0, "volume": 1}
        path = build_day_path(bad, 20, rng_for())
        assert len(path) == 20
        assert path[-1] == 102.0
        assert all(99.0 <= p <= 102.0 for p in path)


class TestBuildDayVolumes:
    def test_total_is_conserved_exactly(self):
        volumes = build_day_volumes(50_000, 40, rng_for())
        assert sum(volumes) == 50_000

    def test_fractional_total_conserved_to_rounding(self):
        volumes = build_day_volumes(1234.6, 10, rng_for())
        assert sum(volumes) == round(1234.6)

    def test_all_non_negative(self):
        volumes = build_day_volumes(50_000, 40, rng_for())
        assert all(v >= 0 for v in volumes)

    def test_deterministic_same_seed(self):
        assert build_day_volumes(50_000, 40, rng_for()) == build_day_volumes(
            50_000, 40, rng_for()
        )

    def test_jitter_varies_across_ticks(self):
        volumes = build_day_volumes(1_000_000, 50, rng_for())
        assert len(set(volumes)) > 1

    def test_zero_total_gives_zero_ticks(self):
        assert build_day_volumes(0.0, 5, rng_for()) == [0.0] * 5

    def test_negative_total_gives_zero_ticks(self):
        assert build_day_volumes(-10.0, 3, rng_for()) == [0.0] * 3

    def test_zero_points_returns_empty(self):
        assert build_day_volumes(50_000, 0, rng_for()) == []

    def test_single_point_takes_the_whole_total(self):
        assert build_day_volumes(50_000, 1, rng_for()) == [50_000.0]
