"""ATR + KDJ indicators and D2 condition fields (D2 contract §2/§6).

Golden vectors: the ``EXPECTED_*`` values below were computed ONCE at
development time by an INDEPENDENT reference implementation (exact
``fractions.Fraction`` arithmetic over the same embedded OHLC series,
written fresh from the textbook formulas — Wilder 1978 ATR; Chinese-
convention KDJ with 50-seeded K/D) and frozen into this file. The contract
fixes the recursions (Wilder TR smoothing; RSV 递推 with K/D 初值 50,
J = 3K − 2D), so hand-computed vectors are the verification source
(contract §2: 预算值内嵌, 无运行时依赖). Our float implementation matched
the exact-rational reference to < 1e-12 relative at every index; the
assertions here use rel=1e-9. Tiny closed-form cases (hand arithmetic)
anchor the same formulas inside the test itself.

Also covers: warm-up alignment, point == series[-1] parity, KDJ cross
boundary semantics (strict on the current bar, inclusive on the previous),
the FIELD_SPECS validation matrix for kdj_cross/atr_pct (defaults, bounds,
forbidden/required value, unknown params), evaluator parity with the pure
functions, warm-up False, the pinned-registry split (FIELD_SPECS /
D1_FIELD_SPECS / ALL_FIELD_SPECS all untouched; the D2 pair rides
ACTIVE_FIELD_SPECS), unchanged live capacity (240 bars / 14520s), and
both engine paths — the backtest's precomputed-columns form and the live
1-second ring buffer (contract §2: 实时与历史同函数复用).
"""

from __future__ import annotations

import pytest

from app.indicators import (
    ACTIVE_FIELD_SPECS,
    ALL_FIELD_SPECS,
    D1_FIELD_SPECS,
    D2_FIELD_SPECS,
    FIELD_SPECS,
    aggregate_minute_bars,
    atr,
    atr_series,
    build_series_context,
    evaluate_condition_group,
    evaluate_group_at,
    kdj,
    kdj_series,
    max_condition_warmup_bars,
    required_history_seconds,
    validate_condition_group,
)
from app.market.cache import PriceCache

# Deterministic 60-bar OHLC series (random.Random(11): ±2% multiplicative
# close walk from 100 with 0.1-1.5% wicks, rounded 4dp) — embedded verbatim
# so no RNG is involved at test time.
HIGHS = [
    100.4777, 100.374, 101.2099, 102.1985, 101.7928, 101.9924, 102.7341, 101.8393,
    101.7635, 101.9761, 102.9546, 102.0362, 101.3478, 99.0128, 100.5843, 101.8508,
    98.9761, 100.9845, 102.4959, 103.8609, 104.3678, 102.7265, 100.7763, 101.4272,
    98.6594, 98.3393, 96.4791, 95.6567, 95.5315, 94.8484, 95.4812, 96.7004,
    96.8967, 97.0557, 96.9701, 97.3802, 95.8587, 98.6174, 99.7774, 101.0278,
    101.9603, 100.9126, 100.2515, 99.0774, 98.4064, 99.1331, 98.6389, 99.2442,
    100.0023, 101.9085, 102.8585, 100.9353, 100.1346, 101.1416, 101.5167, 103.1864,
    103.0638, 100.9821, 101.7665, 102.7857,
]
LOWS = [
    99.778, 98.3626, 99.7828, 101.5001, 100.3706, 100.7404, 101.6189, 100.4213,
    100.4896, 100.8751, 100.9698, 100.2716, 99.6139, 97.0964, 99.3503, 99.9604,
    97.357, 99.9046, 102.0572, 102.4652, 102.7526, 100.5062, 99.0947, 99.7562,
    96.9997, 96.6937, 95.0363, 93.9667, 92.9271, 93.3121, 95.1007, 94.9069,
    95.4146, 94.9581, 96.1439, 96.1236, 94.615, 96.4235, 97.9141, 100.5158,
    100.7417, 100.3773, 98.7396, 98.1969, 95.6682, 97.0846, 97.4516, 98.9725,
    97.5799, 100.3236, 101.06, 99.2692, 98.4531, 99.1398, 99.0206, 100.843,
    101.0575, 98.5636, 99.7541, 100.2308,
]
CLOSES = [
    100.0, 99.8095, 100.0481, 101.7458, 101.606, 101.6379, 101.9932, 100.7067,
    100.7547, 101.2782, 102.4651, 100.8016, 100.0089, 98.3714, 99.5898, 100.3604,
    98.5213, 100.4216, 102.2885, 102.9183, 103.394, 101.9775, 99.9991, 100.1126,
    98.3488, 97.1301, 96.1275, 94.3206, 94.1845, 93.9605, 95.2475, 95.3204,
    95.8553, 95.8544, 96.4773, 96.3126, 95.458, 97.3582, 99.2886, 100.6398,
    101.4764, 100.7266, 99.6374, 98.7966, 97.0982, 98.1324, 97.7414, 99.0964,
    98.6466, 100.454, 101.8495, 99.8147, 98.6557, 100.2747, 100.1543, 102.0787,
    101.6599, 99.9237, 100.4411, 101.5601,
]

# Reference golden values: {period: {idx: atr}} — first valid index = period.
EXPECTED_ATR = {
    14: {
        14: 1.748792857142857,
        23: 1.993924135634139,
        40: 1.9209131389223615,
        59: 2.2325993099780894,
    },
    5: {
        5: 1.65262,
        14: 1.98972420350976,
        40: 1.8600616009721769,
        59: 2.4319265941237753,
    },
}

# Reference golden values: {n: {idx: (K, D, J)}} — first valid index = n - 1.
EXPECTED_KDJ = {
    9: {
        8: (51.57344923557894, 50.524483078526316, 53.67138154968419),
        14: (35.75031432057795, 40.93325204715595, 25.38443886742194),
        40: (84.83262948795046, 73.55711902076646, 107.38365042231847),
        59: (55.4062673651121, 55.84775525223451, 54.523291590867274),
    },
    5: {
        4: (61.5179401618047, 53.839313387268234, 76.87519371087764),
        10: (53.01817707420895, 49.97661560904018, 59.10130000454649),
        40: (82.26186737860411, 70.72878264407112, 105.32803684767009),
        59: (52.42462080019166, 51.02141791976706, 55.231026561040856),
    },
}

# Reference K/D cross bars for kdj(9, 3, 3) on the embedded series.
KDJ9_GOLDEN_CROSSES = {15, 17, 31, 49, 55}
KDJ9_DEATH_CROSSES = {11, 16, 22, 43, 52, 57}


class TestAtrGolden:
    @pytest.mark.parametrize("period", sorted(EXPECTED_ATR))
    def test_matches_reference_vectors(self, period):
        series = atr_series(HIGHS, LOWS, CLOSES, period)
        for idx, expected in EXPECTED_ATR[period].items():
            assert series[idx] == pytest.approx(expected, rel=1e-9)

    @pytest.mark.parametrize("period", sorted(EXPECTED_ATR))
    def test_warmup_alignment(self, period):
        """First ATR (SMA seed of the first ``period`` TRs) lands at index
        ``period``; everything before is None."""
        series = atr_series(HIGHS, LOWS, CLOSES, period)
        assert all(v is None for v in series[:period])
        assert series[period] is not None
        assert all(v is not None for v in series[period:])

    def test_point_is_last_series_element(self):
        series = atr_series(HIGHS, LOWS, CLOSES, 14)
        assert atr(HIGHS, LOWS, CLOSES, 14) == series[-1]
        assert atr([], [], []) is None
        # Needs period + 1 bars (TR needs the previous close).
        assert atr(HIGHS[:14], LOWS[:14], CLOSES[:14], 14) is None
        assert atr(HIGHS[:15], LOWS[:15], CLOSES[:15], 14) is not None

    def test_hand_computed_tiny_case(self):
        """period=2 over 4 bars, verified by hand arithmetic.

        TR1 = max(12-9, |12-9|, |9-9|) = 3; TR2 = max(11-7, |11-11|,
        |7-11|) = 4 → seed ATR = 3.5. TR3 = max(13-10, |13-8|, |10-8|) = 5
        → ATR = (3.5*1 + 5)/2 = 4.25.
        """
        highs, lows, closes = [10, 12, 11, 13], [8, 9, 7, 10], [9, 11, 8, 12]
        series = atr_series(highs, lows, closes, 2)
        assert series == [None, None, 3.5, 4.25]

    def test_gap_dominates_true_range(self):
        """A gap beyond the bar's own range widens TR via |high-prev_close|."""
        # Bar 1 gaps up: high-low = 1, but |high - prev_close| = 10.
        series = atr_series([10, 20], [9, 19], [10, 19.5], 1)
        assert series[1] == pytest.approx(10.0)

    def test_flat_series_zero_and_constant_range(self):
        flat = [100.0] * 20
        assert atr(flat, flat, flat, 14) == pytest.approx(0.0, abs=1e-12)
        highs = [c + 1.0 for c in flat]
        lows = [c - 1.0 for c in flat]
        series = atr_series(highs, lows, flat, 14)
        for v in series[14:]:
            assert v == pytest.approx(2.0, rel=1e-12)


class TestKdjGolden:
    @pytest.mark.parametrize("n", sorted(EXPECTED_KDJ))
    def test_matches_reference_vectors(self, n):
        k_series, d_series, j_series = kdj_series(HIGHS, LOWS, CLOSES, n)
        for idx, (ek, ed, ej) in EXPECTED_KDJ[n].items():
            assert k_series[idx] == pytest.approx(ek, rel=1e-9)
            assert d_series[idx] == pytest.approx(ed, rel=1e-9)
            assert j_series[idx] == pytest.approx(ej, rel=1e-9)

    @pytest.mark.parametrize("n", sorted(EXPECTED_KDJ))
    def test_warmup_alignment(self, n):
        """RSV needs ``n`` bars — K/D/J first resolve at index ``n - 1``."""
        k_series, d_series, j_series = kdj_series(HIGHS, LOWS, CLOSES, n)
        for series in (k_series, d_series, j_series):
            assert all(v is None for v in series[: n - 1])
            assert all(v is not None for v in series[n - 1 :])

    def test_point_is_last_series_element(self):
        k_series, d_series, j_series = kdj_series(HIGHS, LOWS, CLOSES, 9)
        assert kdj(HIGHS, LOWS, CLOSES, 9) == (k_series[-1], d_series[-1], j_series[-1])
        assert kdj([], [], []) == (None, None, None)
        assert kdj(HIGHS[:8], LOWS[:8], CLOSES[:8], 9) == (None, None, None)

    def test_hand_computed_tiny_case(self):
        """n=2 on two rising bars: RSV=100 → K=200/3, D=500/9, J=800/9."""
        k_val, d_val, j_val = kdj([10, 20], [10, 20], [10, 20], 2)
        assert k_val == pytest.approx(200.0 / 3.0, rel=1e-12)
        assert d_val == pytest.approx(500.0 / 9.0, rel=1e-12)
        assert j_val == pytest.approx(800.0 / 9.0, rel=1e-12)

    def test_flat_window_rsv_is_neutral_50(self):
        """HHV == LLV (flat window) yields the neutral RSV 50 — K/D/J stay 50."""
        flat = [100.0] * 15
        k_series, d_series, j_series = kdj_series(flat, flat, flat, 9)
        for i in range(8, 15):
            assert k_series[i] == pytest.approx(50.0)
            assert d_series[i] == pytest.approx(50.0)
            assert j_series[i] == pytest.approx(50.0)

    def test_j_is_3k_minus_2d_and_kd_bounded(self):
        k_series, d_series, j_series = kdj_series(HIGHS, LOWS, CLOSES, 9)
        for k_v, d_v, j_v in zip(k_series[8:], d_series[8:], j_series[8:]):
            assert j_v == pytest.approx(3.0 * k_v - 2.0 * d_v, rel=1e-12)
            # RSV ∈ [0, 100] and 50-seeded recursions keep K/D in [0, 100];
            # J may legitimately exceed the band (107.38 at idx 40).
            assert 0.0 <= k_v <= 100.0
            assert 0.0 <= d_v <= 100.0


class TestValidationMatrix:
    @pytest.mark.parametrize(
        "cond",
        [
            {"field": "kdj_cross", "op": "above"},  # default n=9
            {"field": "kdj_cross", "op": "below", "params": {"n": 5}},
            {"field": "kdj_cross", "op": "above", "params": {"n": 30}},
            {"field": "atr_pct", "op": "above", "value": 2},  # default period=14
            {"field": "atr_pct", "op": "below", "value": 0.5, "params": {"period": 5}},
            {"field": "atr_pct", "op": "above", "value": 1.5, "params": {"period": 50}},
        ],
    )
    def test_valid(self, cond):
        assert validate_condition_group({"all": [cond]}) is None

    @pytest.mark.parametrize(
        "cond,fragment",
        [
            ({"field": "kdj_cross", "op": "above", "value": 1}, "takes no value"),
            ({"field": "kdj_cross", "op": "above", "params": {"n": 4}}, "between 5 and 30"),
            ({"field": "kdj_cross", "op": "above", "params": {"n": 31}}, "between 5 and 30"),
            ({"field": "kdj_cross", "op": "above", "params": {"n": 9.5}}, "must be an integer"),
            ({"field": "kdj_cross", "op": "above", "params": {"speed": 3}}, "unknown params"),
            ({"field": "kdj_cross", "op": "sideways"}, "op must be"),
            ({"field": "atr_pct", "op": "above"}, "requires a numeric value"),
            ({"field": "atr_pct", "op": "above", "value": 0}, "greater than 0"),
            ({"field": "atr_pct", "op": "below", "value": -1}, "greater than 0"),
            ({"field": "atr_pct", "op": "above", "value": "big"}, "must be a number"),
            (
                {"field": "atr_pct", "op": "above", "value": 2, "params": {"period": 4}},
                "between 5 and 50",
            ),
            (
                {"field": "atr_pct", "op": "above", "value": 2, "params": {"period": 51}},
                "between 5 and 50",
            ),
            (
                {"field": "atr_pct", "op": "above", "value": 2, "params": {"period": 14.5}},
                "must be an integer",
            ),
            (
                {"field": "atr_pct", "op": "above", "value": 2, "params": {"window": 14}},
                "unknown params",
            ),
            (
                {"field": "atr_pct", "op": "above", "value": 2, "extra": True},
                "unknown condition keys",
            ),
        ],
    )
    def test_invalid(self, cond, fragment):
        error = validate_condition_group({"all": [cond]})
        assert error is not None and fragment in error

    def test_registry_split_keeps_all_existing_pins(self):
        """P2/D1 registries stay byte-identical; the D2 pair rides ACTIVE."""
        assert "kdj_cross" not in FIELD_SPECS and "atr_pct" not in FIELD_SPECS
        assert "kdj_cross" not in D1_FIELD_SPECS and "atr_pct" not in D1_FIELD_SPECS
        # The D1 union pin is untouched — ALL_FIELD_SPECS did not grow.
        assert set(ALL_FIELD_SPECS) == set(FIELD_SPECS) | set(D1_FIELD_SPECS)
        assert set(D2_FIELD_SPECS) == {"kdj_cross", "atr_pct"}
        assert set(ACTIVE_FIELD_SPECS) == set(ALL_FIELD_SPECS) | set(D2_FIELD_SPECS)

    def test_warmup_derivation_covers_new_fields_within_capacity(self):
        # kdj_cross warm-up = n.hi + 1 = 31; atr_pct = period.hi + 1 = 51 —
        # both inside the P2 maximum (240), so live capacity is unchanged.
        assert max_condition_warmup_bars() == 240
        assert required_history_seconds() == 14_520


def _bars_from_ohlc(highs, lows, closes) -> list[dict]:
    return [
        {"time": i * 60, "open": c, "high": h, "low": lo, "close": c, "volume": 1.0}
        for i, (h, lo, c) in enumerate(zip(highs, lows, closes))
    ]


class TestEvaluation:
    def test_kdj_cross_boundary_semantics(self):
        """prev <= / current > (above); equality on the CURRENT bar is no cross."""
        entry = {"all": [{"field": "kdj_cross", "op": "above", "params": {"n": 5}}]}
        ctx = {
            ("kdjk", 5): [None, 40.0, 50.0, 60.0],
            ("kdjd", 5): [None, 45.0, 50.0, 55.0],
        }
        quote = {"price": 1.0, "day_change_percent": 0.0}
        # prev(50 <= 50, inclusive equality), now(60 > 55) -> golden cross
        # fires at idx 3.
        assert evaluate_group_at(entry, ctx, 3, quote) is True
        # prev equality counts (inclusive), current equality does not (strict).
        assert evaluate_group_at(entry, ctx, 2, quote) is False  # 50 > 50 fails
        below = {"all": [{"field": "kdj_cross", "op": "below", "params": {"n": 5}}]}
        ctx_down = {
            ("kdjk", 5): [55.0, 45.0],
            ("kdjd", 5): [55.0, 50.0],
        }
        assert evaluate_group_at(below, ctx_down, 1, quote) is True
        # Warm-up (None legs) is always False.
        assert evaluate_group_at(entry, ctx, 1, quote) is False

    @pytest.mark.parametrize(
        "op,expected",
        [("above", KDJ9_GOLDEN_CROSSES), ("below", KDJ9_DEATH_CROSSES)],
    )
    def test_kdj_cross_fires_exactly_at_reference_bars(self, op, expected):
        """End-to-end (live shape): fires on exactly the reference cross bars."""
        entry = {"all": [{"field": "kdj_cross", "op": op, "params": {"n": 9}}]}
        bars = _bars_from_ohlc(HIGHS, LOWS, CLOSES)
        fired = {
            i
            for i in range(len(bars))
            if evaluate_condition_group(
                entry, bars[: i + 1], {"price": CLOSES[i], "day_change_percent": 0.0}
            )
        }
        assert fired == expected

    def test_atr_pct_matches_pure_functions(self):
        """Threshold is inclusive and equals atr/close*100 of the last bar."""
        level = atr(HIGHS, LOWS, CLOSES, 14) / CLOSES[-1] * 100.0
        bars = _bars_from_ohlc(HIGHS, LOWS, CLOSES)
        quote = {"price": CLOSES[-1], "day_change_percent": 0.0}

        def fires(op, value):
            entry = {"all": [{"field": "atr_pct", "op": op, "value": value,
                              "params": {"period": 14}}]}
            return evaluate_condition_group(entry, bars, quote)

        assert fires("above", level) is True  # exactly at the level (>=)
        assert fires("above", level + 1e-6) is False
        assert fires("below", level) is True  # <= mirror
        assert fires("below", level - 1e-6) is False

    def test_warmup_false_for_both_fields(self):
        quote = {"price": 1e9, "day_change_percent": 0.0}
        short = _bars_from_ohlc(HIGHS[:10], LOWS[:10], CLOSES[:10])
        atr_entry = {"all": [{"field": "atr_pct", "op": "above", "value": 0.0001}]}
        assert evaluate_condition_group(atr_entry, short, quote) is False  # period 14
        kdj_entry = {"all": [{"field": "kdj_cross", "op": "above"}]}  # n 9
        tiny = _bars_from_ohlc(HIGHS[:9], LOWS[:9], CLOSES[:9])
        # 9 bars resolve K/D at idx 8 but the cross also reads idx 7 (None).
        assert evaluate_condition_group(kdj_entry, tiny, quote) is False
        assert evaluate_condition_group(kdj_entry, [], quote) is False

    def test_series_context_keys_and_columns_fast_path_parity(self):
        """The backtest columns form builds the same series the pure
        functions produce — live/backtest read identical values."""
        entry = {
            "all": [
                {"field": "kdj_cross", "op": "above", "params": {"n": 9}},
                {"field": "atr_pct", "op": "above", "value": 1, "params": {"period": 14}},
            ]
        }
        columns = {"closes": CLOSES, "highs": HIGHS, "lows": LOWS}
        ctx = build_series_context(entry, columns)
        assert set(ctx) == {("kdjk", 9), ("kdjd", 9), ("atrp", 14)}
        k_series, d_series, _j = kdj_series(HIGHS, LOWS, CLOSES, 9)
        assert ctx[("kdjk", 9)] == k_series
        assert ctx[("kdjd", 9)] == d_series
        atr_vals = atr_series(HIGHS, LOWS, CLOSES, 14)
        for i, v in enumerate(ctx[("atrp", 14)]):
            if atr_vals[i] is None:
                assert v is None
            else:
                assert v == pytest.approx(atr_vals[i] / CLOSES[i] * 100.0, rel=1e-12)
        # Bar-dict form (live/history-daily shape) produces the same context.
        ctx_bars = build_series_context(entry, _bars_from_ohlc(HIGHS, LOWS, CLOSES))
        assert ctx_bars[("kdjk", 9)] == ctx[("kdjk", 9)]
        assert ctx_bars[("atrp", 14)] == ctx[("atrp", 14)]

    @pytest.mark.parametrize("field_name", sorted(D2_FIELD_SPECS))
    def test_upper_bounds_satisfiable_on_full_live_ring_buffer(self, field_name):
        """The live-capacity invariant extends to the D2 fields (contract §0)."""
        spec = D2_FIELD_SPECS[field_name]
        params = {name: ps.hi for name, ps in spec.params.items()}
        cond = {"field": field_name, "op": spec.ops[0], "params": params}
        if spec.value_rule == "required_positive":
            cond["value"] = 1.0
        entry = {"all": [cond]}
        assert validate_condition_group(entry) is None

        cache = PriceCache(history_capacity=required_history_seconds())
        base = 1_700_000_000 - (1_700_000_000 % 60) + 37
        for i in range(required_history_seconds() + 90):
            cache.update("PIN", 100.0 + (i % 7) * 0.05, timestamp=float(base + i))
        bars_1m = aggregate_minute_bars(cache.get_history("PIN"))
        ctx = build_series_context(entry, bars_1m)
        assert ctx
        idx = len(bars_1m) - 1
        for series in ctx.values():
            # Cross detectors read idx and idx-1; both must be warmed up.
            assert series[idx] is not None and series[idx - 1] is not None
