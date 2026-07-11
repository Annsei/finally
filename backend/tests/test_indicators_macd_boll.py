"""MACD + Bollinger indicators and D1 condition fields (contract §4/§6).

Golden vectors: the ``EXPECTED_*`` values below were computed ONCE at
development time with **pandas-ta-classic** (``ta.macd(s, fast, slow,
signal)`` and ``ta.bbands(s, length, std=k, ddof=0)``) on the embedded
``CLOSES`` series and frozen into this file — pandas-ta is a dev-time
verification source only, never a runtime or test-time dependency
(contract §4: 金向量校验, 不引入运行时依赖). Our implementation matched
pandas-ta to < 1.5e-13 absolute across every index of both series; the
assertions here use rel=1e-9.

Also covers: warm-up alignment (None exactly where pandas-ta had NaN),
point == series[-1] parity, cross boundary semantics (strict on the current
bar, inclusive on the previous), the FIELD_SPECS validation matrix for
macd_cross/boll_break (defaults, bounds, float k, fast<slow, forbidden
value), evaluator parity with the pure functions, warm-up False, the
pinned-registry split (FIELD_SPECS untouched, ALL_FIELD_SPECS extended),
and live ring-buffer satisfiability for both new fields' upper bounds.
"""

from __future__ import annotations

import pytest

from app.indicators import (
    ALL_FIELD_SPECS,
    D1_FIELD_SPECS,
    FIELD_SPECS,
    aggregate_minute_bars,
    bollinger,
    bollinger_series,
    build_series_context,
    evaluate_condition_group,
    evaluate_group_at,
    macd,
    macd_series,
    max_condition_warmup_bars,
    required_history_seconds,
    rolling_std_series,
    sma_series,
    validate_condition_group,
)
from app.market.cache import PriceCache

# Deterministic 60-close series (random.Random(7) multiplicative walk from
# 100, rounded 4dp) — embedded verbatim so no RNG is involved at test time.
CLOSES = [
    100.0, 99.2953, 97.9085, 98.4996, 96.815, 96.954, 96.4331, 94.7282,
    94.7564, 93.0034, 92.7566, 91.1607, 89.6683, 89.3976, 90.5664, 89.2036,
    88.2161, 88.6658, 90.2537, 90.5321, 90.158, 91.8755, 90.2092, 91.5027,
    90.7326, 89.4415, 88.0741, 87.3994, 88.5046, 87.3743, 87.6595, 88.1466,
    87.6967, 87.8642, 86.3276, 84.8069, 83.8094, 84.4142, 84.1697, 83.544,
    83.8299, 83.6729, 83.0027, 83.9801, 84.6486, 83.7821, 84.0315, 84.1162,
    85.3784, 86.162, 85.4311, 87.072, 85.7418, 85.461, 86.34, 85.1381,
    85.1005, 83.532, 84.0941, 84.9841,
]

# pandas-ta-classic golden values: {(fast, slow, signal): {idx: (macd, signal, hist)}}
EXPECTED_MACD = {
    (12, 26, 9): {
        33: (-1.8942136308036908, -2.0343844104725894, 0.14017077966889868),
        45: (-2.0129139784533265, -2.1844559723767976, 0.17154199392347103),
        59: (-0.6724378690016977, -0.8306355848152094, 0.15819771581351172),
    },
    (5, 10, 4): {
        12: (-2.165006081901879, -1.9717106481438407, -0.19329543375803837),
        45: (-0.3919298090804517, -0.5571642132937864, 0.16523440421333468),
        59: (-0.19852460294488594, -0.1554185020186868, -0.04310610092619915),
    },
}

# pandas-ta-classic golden values: {(period, k): {idx: (mid, upper, lower)}}
EXPECTED_BOLL = {
    (20, 2.0): {
        19: (93.44072, 101.06029689208528, 85.82114310791472),
        40: (87.36913000000001, 92.47487231002702, 82.263387689973),
        59: (84.774955, 86.87298441969364, 82.67692558030637),
    },
    (10, 1.5): {
        9: (96.83935, 99.97179517831118, 93.70690482168881),
        40: (85.46092, 88.10157698200278, 82.82026301799722),
        59: (85.28947, 86.72857863982709, 83.8503613601729),
    },
}


class TestMacdGolden:
    @pytest.mark.parametrize("params", sorted(EXPECTED_MACD))
    def test_matches_pandas_ta_vectors(self, params):
        fast, slow, signal = params
        macd_line, signal_line, hist = macd_series(CLOSES, fast, slow, signal)
        for idx, (m, s, h) in EXPECTED_MACD[params].items():
            assert macd_line[idx] == pytest.approx(m, rel=1e-9)
            assert signal_line[idx] == pytest.approx(s, rel=1e-9)
            assert hist[idx] == pytest.approx(h, rel=1e-9)

    @pytest.mark.parametrize("params", sorted(EXPECTED_MACD))
    def test_warmup_alignment_matches_pandas_ta(self, params):
        """None exactly where pandas-ta had NaN: macd at slow-1, signal at
        slow+signal-2."""
        fast, slow, signal = params
        macd_line, signal_line, hist = macd_series(CLOSES, fast, slow, signal)
        assert macd_line[slow - 2] is None and macd_line[slow - 1] is not None
        first_sig = slow + signal - 2
        assert signal_line[first_sig - 1] is None
        assert signal_line[first_sig] is not None
        assert hist[first_sig - 1] is None and hist[first_sig] is not None

    def test_point_is_last_series_element(self):
        macd_line, signal_line, hist = macd_series(CLOSES, 12, 26, 9)
        assert macd(CLOSES, 12, 26, 9) == (macd_line[-1], signal_line[-1], hist[-1])
        assert macd([]) == (None, None, None)
        # Enough closes for the macd line but not the signal line.
        short = CLOSES[:30]
        m, s, h = macd(short, 12, 26, 9)
        assert m is not None and s is None and h is None


class TestBollingerGolden:
    @pytest.mark.parametrize("params", sorted(EXPECTED_BOLL))
    def test_matches_pandas_ta_vectors(self, params):
        period, k = params
        mid, upper, lower = bollinger_series(CLOSES, period, k)
        for idx, (em, eu, el) in EXPECTED_BOLL[params].items():
            assert mid[idx] == pytest.approx(em, rel=1e-9)
            assert upper[idx] == pytest.approx(eu, rel=1e-9)
            assert lower[idx] == pytest.approx(el, rel=1e-9)

    def test_warmup_and_point_parity(self):
        mid, upper, lower = bollinger_series(CLOSES, 20, 2.0)
        assert mid[18] is None and mid[19] is not None
        assert bollinger(CLOSES, 20, 2.0) == (mid[-1], upper[-1], lower[-1])
        assert bollinger(CLOSES[:5], 20) == (None, None, None)

    def test_population_std_not_sample(self):
        """ddof=0 (母体标准差): a constant-plus-one-jump window pins it."""
        values = [1.0, 1.0, 1.0, 5.0]
        std = rolling_std_series(values, 4)[-1]
        # population std = sqrt(mean((x-2)^2)) = sqrt(12/4); sample would be sqrt(12/3)
        assert std == pytest.approx((12.0 / 4.0) ** 0.5, rel=1e-12)


class TestValidationMatrix:
    @pytest.mark.parametrize(
        "cond",
        [
            {"field": "macd_cross", "op": "above"},  # all defaults (12/26/9)
            {"field": "macd_cross", "op": "below", "params": {"fast": 5, "slow": 10, "signal": 4}},
            {"field": "macd_cross", "op": "above", "params": {"slow": 30}},
            {"field": "boll_break", "op": "above"},  # defaults (20, 2)
            {"field": "boll_break", "op": "below", "params": {"period": 10, "k": 1.5}},
            {"field": "boll_break", "op": "above", "params": {"k": 4}},  # int k ok
            {"field": "boll_break", "op": "above", "params": {"k": 0.5}},
        ],
    )
    def test_valid(self, cond):
        assert validate_condition_group({"all": [cond]}) is None

    @pytest.mark.parametrize(
        "cond,fragment",
        [
            ({"field": "macd_cross", "op": "above", "value": 1}, "takes no value"),
            ({"field": "boll_break", "op": "below", "value": 2}, "takes no value"),
            (
                {"field": "macd_cross", "op": "above", "params": {"fast": 26, "slow": 26}},
                "'fast' must be less than 'slow'",
            ),
            (
                {"field": "macd_cross", "op": "above", "params": {"fast": 30, "slow": 12}},
                "'fast' must be less than 'slow'",
            ),
            (
                {"field": "macd_cross", "op": "above", "params": {"signal": 1}},
                "between 2 and 60",
            ),
            (
                {"field": "macd_cross", "op": "above", "params": {"signal": 9.5}},
                "must be an integer",
            ),
            (
                {"field": "macd_cross", "op": "above", "params": {"speed": 3}},
                "unknown params",
            ),
            ({"field": "boll_break", "op": "above", "params": {"k": 4.5}}, "between 0.5 and 4"),
            ({"field": "boll_break", "op": "above", "params": {"k": 0.4}}, "between 0.5 and 4"),
            ({"field": "boll_break", "op": "above", "params": {"k": "wide"}}, "must be a number"),
            (
                {"field": "boll_break", "op": "above", "params": {"period": 4}},
                "between 5 and 120",
            ),
            (
                {"field": "boll_break", "op": "above", "params": {"period": 20.5}},
                "must be an integer",
            ),
            ({"field": "macd_cross", "op": "sideways"}, "op must be"),
        ],
    )
    def test_invalid(self, cond, fragment):
        error = validate_condition_group({"all": [cond]})
        assert error is not None and fragment in error

    def test_registry_split_keeps_p2_pin_and_extends_lookup(self):
        """FIELD_SPECS stays the pinned P2 nine; the D1 pair rides the merge."""
        assert "macd_cross" not in FIELD_SPECS and "boll_break" not in FIELD_SPECS
        assert set(D1_FIELD_SPECS) == {"macd_cross", "boll_break"}
        assert set(ALL_FIELD_SPECS) == set(FIELD_SPECS) | set(D1_FIELD_SPECS)

    def test_warmup_derivation_covers_new_fields_within_capacity(self):
        # macd_cross warm-up = slow.hi + signal.hi = 180; boll_break = 120 —
        # both inside the P2 maximum, so live capacity must be unchanged.
        assert max_condition_warmup_bars() == 240
        assert required_history_seconds() == 14_520


def _bars_from_closes(closes: list[float]) -> list[dict]:
    return [
        {"time": i * 60, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for i, c in enumerate(closes)
    ]


class TestEvaluation:
    def test_macd_cross_boundary_semantics(self):
        """prev <= / current > (above); equality on the CURRENT bar is no cross."""
        entry = {"all": [{"field": "macd_cross", "op": "above",
                          "params": {"fast": 2, "slow": 3, "signal": 2}}]}
        ctx = {
            ("macdl", 2, 3, 2): [None, -1.0, 0.0, 1.0],
            ("macds", 2, 3, 2): [None, -0.5, 0.0, 0.5],
        }
        quote = {"price": 1.0, "day_change_percent": 0.0}
        # prev(-1 <= -0.5), now(1 > 0.5) -> golden cross fires at idx 3.
        assert evaluate_group_at(entry, ctx, 3, quote) is True
        # prev equality counts (inclusive), current equality does not (strict).
        assert evaluate_group_at(entry, ctx, 2, quote) is False  # 0.0 > 0.0 fails
        below = {"all": [{"field": "macd_cross", "op": "below",
                          "params": {"fast": 2, "slow": 3, "signal": 2}}]}
        ctx_down = {
            ("macdl", 2, 3, 2): [0.5, -0.5],
            ("macds", 2, 3, 2): [0.5, 0.0],
        }
        assert evaluate_group_at(below, ctx_down, 1, quote) is True
        # Warm-up (None legs) is always False.
        assert evaluate_group_at(entry, ctx, 1, quote) is False

    def test_macd_cross_fires_on_real_series(self):
        """End-to-end through build_series_context on a crafted V shape."""
        closes = [100.0 - i for i in range(20)] + [81.0 + 2.0 * i for i in range(20)]
        entry = {"all": [{"field": "macd_cross", "op": "above",
                          "params": {"fast": 3, "slow": 6, "signal": 3}}]}
        bars = _bars_from_closes(closes)
        fired_at = [
            i
            for i in range(len(closes))
            if evaluate_condition_group(
                entry, bars[: i + 1], {"price": closes[i], "day_change_percent": 0.0}
            )
        ]
        assert fired_at  # the V-recovery produces a golden cross
        assert all(i >= 20 for i in fired_at)  # never during the decline

    def test_boll_break_matches_pure_function(self):
        entry_up = {"all": [{"field": "boll_break", "op": "above",
                             "params": {"period": 10, "k": 1.5}}]}
        entry_down = {"all": [{"field": "boll_break", "op": "below",
                               "params": {"period": 10, "k": 1.5}}]}
        bars = _bars_from_closes(CLOSES)
        ctx = build_series_context(entry_up, {"closes": CLOSES, "highs": CLOSES, "lows": CLOSES})
        assert ("sma", 10) in ctx and ("bstd", 10) in ctx
        idx = len(CLOSES) - 1
        _mid, upper, lower = bollinger(CLOSES, 10, 1.5)
        # Exactly at the band is a break (inclusive >=/<=), just inside is not.
        assert evaluate_group_at(entry_up, ctx, idx, {"price": upper}) is True
        assert evaluate_group_at(entry_up, ctx, idx, {"price": upper - 0.01}) is False
        assert evaluate_group_at(entry_down, ctx, idx, {"price": lower}) is True
        assert evaluate_group_at(entry_down, ctx, idx, {"price": lower + 0.01}) is False
        # The one-shot evaluator agrees with the two-step form.
        assert evaluate_condition_group(entry_up, bars, {"price": upper}) is True

    def test_boll_break_warmup_false(self):
        entry = {"all": [{"field": "boll_break", "op": "above"}]}  # period 20
        short = _bars_from_closes(CLOSES[:10])
        assert evaluate_condition_group(entry, short, {"price": 1e9}) is False

    @pytest.mark.parametrize("field_name", sorted(D1_FIELD_SPECS))
    def test_upper_bounds_satisfiable_on_full_live_ring_buffer(self, field_name):
        """The live-capacity invariant extends to the D1 fields (contract §0)."""
        spec = D1_FIELD_SPECS[field_name]
        params = {name: ps.hi for name, ps in spec.params.items()}
        if field_name == "macd_cross":
            params["fast"] = spec.params["fast"].lo  # fast must stay < slow
        cond = {"field": field_name, "op": spec.ops[0], "params": params}
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

    def test_boll_break_series_reuses_shared_sma(self):
        """The band recomposition uses the SAME ("sma", n) series 'ma' reads."""
        entry = {
            "all": [
                {"field": "boll_break", "op": "above", "params": {"period": 10}},
                {"field": "ma", "op": "above", "params": {"period": 10}},
            ]
        }
        ctx = build_series_context(entry, {"closes": CLOSES, "highs": CLOSES, "lows": CLOSES})
        assert set(ctx) == {("sma", 10), ("bstd", 10)}
        assert ctx[("sma", 10)] == sma_series(CLOSES, 10)
