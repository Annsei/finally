"""Tests for app.indicators (P2 §2) — pure indicator math and conditions.

Covers:
- aggregate_minute_bars: 60s bucketing, OHLCV merge, forming-minute drop
- SMA/EMA/RSI/rolling-window series and point functions on known vectors
- validate_condition_group: the strict whitelist 400 matrix (unknown
  field/op/extra keys/param bounds/nesting limits) and all-valid entries
- validate_exits / validate_sizing shape rules
- evaluation semantics: inclusive price/day-change compares (rules parity),
  MA offset, golden/death crosses on the completed-bar series, window
  breakout/breakdown, pullback, all/any groups, PriceUpdate-like attribute
  quotes, and the warm-up -> False / never-raises guarantee
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.indicators import (
    FIELD_SPECS,
    aggregate_minute_bars,
    build_series_context,
    ema,
    ema_series,
    evaluate_condition_group,
    evaluate_group_at,
    has_any_exit,
    rolling_max_series,
    rolling_min_series,
    rsi,
    rsi_series,
    sma,
    sma_series,
    validate_condition_group,
    validate_exits,
    validate_sizing,
    window_high,
    window_low,
)


def _bars(closes, highs=None, lows=None):
    """Completed minute bars from close values (highs/lows default to close)."""
    highs = closes if highs is None else highs
    lows = closes if lows is None else lows
    return [
        {"time": i * 60, "open": c, "high": h, "low": lo, "close": c, "volume": 0.0}
        for i, (c, h, lo) in enumerate(zip(closes, highs, lows))
    ]


def _group(field, op, value=None, params=None, mode="all"):
    cond = {"field": field, "op": op}
    if value is not None:
        cond["value"] = value
    if params is not None:
        cond["params"] = params
    return {mode: [cond]}


class TestAggregateMinuteBars:
    def test_empty_input(self):
        assert aggregate_minute_bars([]) == []

    def test_single_forming_minute_is_dropped(self):
        ticks = [{"time": 30, "price": 10.0}, {"time": 45, "price": 11.0}]
        assert aggregate_minute_bars(ticks) == []

    def test_tick_aggregation_drops_newest_minute(self):
        ticks = [
            {"time": 0, "price": 10.0},
            {"time": 20, "price": 12.0},
            {"time": 59, "price": 11.0},
            {"time": 60, "price": 11.5},
            {"time": 119, "price": 9.0},
            {"time": 120, "price": 99.0},  # forming minute — dropped
        ]
        bars = aggregate_minute_bars(ticks)
        assert [b["time"] for b in bars] == [0, 60]
        assert bars[0] == {
            "time": 0, "open": 10.0, "high": 12.0, "low": 10.0, "close": 11.0,
            "volume": 0.0,
        }
        assert bars[1]["open"] == 11.5
        assert bars[1]["close"] == 9.0

    def test_unsorted_input_is_sorted_by_time(self):
        ticks = [
            {"time": 61, "price": 5.0},
            {"time": 0, "price": 1.0},
            {"time": 30, "price": 3.0},
            {"time": 120, "price": 9.0},
        ]
        bars = aggregate_minute_bars(ticks)
        assert [b["time"] for b in bars] == [0, 60]
        assert bars[0]["open"] == 1.0
        assert bars[0]["close"] == 3.0

    def test_ohlcv_samples_merge_and_volume_sums(self):
        samples = [
            {"time": 0, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 5},
            {"time": 30, "open": 11.0, "high": 15.0, "low": 10.5, "close": 14.0, "volume": 7},
            {"time": 60, "price": 14.0},
        ]
        bars = aggregate_minute_bars(samples)
        assert len(bars) == 1
        assert bars[0] == {
            "time": 0, "open": 10.0, "high": 15.0, "low": 9.0, "close": 14.0,
            "volume": 12.0,
        }


class TestSmaEma:
    def test_sma_series_known_vector(self):
        assert sma_series([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]

    def test_sma_point_is_series_tail(self):
        closes = [1, 2, 3, 4, 5]
        assert sma(closes, 3) == sma_series(closes, 3)[-1] == 4.0

    def test_sma_insufficient_data(self):
        assert sma([1.0, 2.0], 3) is None
        assert sma([], 3) is None

    def test_ema_series_sma_seed_and_recursion(self):
        # n=3 -> alpha=0.5; seed = mean(2,4,6) = 4; next = 0.5*8 + 0.5*4 = 6
        assert ema_series([2, 4, 6, 8], 3) == [None, None, 4.0, 6.0]

    def test_ema_point_and_insufficient(self):
        assert ema([2, 4, 6, 8], 3) == 6.0
        assert ema([2, 4], 3) is None


class TestRsi:
    def test_all_gains_is_100(self):
        assert rsi([1.0, 2.0, 3.0], 2) == 100.0

    def test_all_losses_is_0(self):
        assert rsi([3.0, 2.0, 1.0], 2) == 0.0

    def test_flat_is_50(self):
        assert rsi([5.0, 5.0, 5.0], 2) == 50.0

    def test_wilder_smoothing_vector(self):
        # n=2, closes [1,2,3,2]: seed avg_gain=1, avg_loss=0 -> 100 at idx 2;
        # idx 3: avg_gain=(1*1+0)/2=0.5, avg_loss=(0*1+1)/2=0.5 -> RS=1 -> 50.
        series = rsi_series([1.0, 2.0, 3.0, 2.0], 2)
        assert series == [None, None, 100.0, 50.0]

    def test_insufficient_data(self):
        assert rsi([1.0] * 14, 14) is None  # needs n+1 closes
        assert rsi_series([], 14) == []


class TestWindows:
    def test_rolling_max_min_series(self):
        assert rolling_max_series([1, 5, 3, 4], 2) == [None, 5.0, 5.0, 4.0]
        assert rolling_min_series([4, 1, 3, 2], 2) == [None, 1.0, 1.0, 2.0]

    def test_window_high_low_points(self):
        bars = _bars([1, 1, 1, 1], highs=[1, 5, 3, 4], lows=[0.5, 0.2, 0.8, 0.6])
        assert window_high(bars, 2) == 4.0
        assert window_low(bars, 2) == 0.6

    def test_window_insufficient(self):
        bars = _bars([1, 2, 3])
        assert window_high(bars, 5) is None
        assert window_low(bars, 5) is None
        assert window_high([], 5) is None


VALID_ENTRIES = [
    pytest.param({"all": [{"field": "day_change_pct", "op": "below", "value": -3}]},
                 id="dip-buyer"),
    pytest.param({"all": [{"field": "window_high", "op": "above",
                           "params": {"minutes": 60}}]}, id="breakout"),
    pytest.param({"all": [{"field": "ma_cross", "op": "above",
                           "params": {"fast": 5, "slow": 20}}]}, id="golden-cross"),
    pytest.param({"all": [{"field": "pullback_from_high_pct", "op": "above",
                           "value": 2, "params": {"minutes": 60}}]}, id="grid-lite"),
    pytest.param({"all": [{"field": "rsi", "op": "below", "value": 30,
                           "params": {"period": 14}}]}, id="rsi-rebound"),
    pytest.param({"all": [
        {"field": "ma", "op": "above", "value": 0, "params": {"period": 30}},
        {"field": "day_change_pct", "op": "above", "value": 0.5},
    ]}, id="trend-rider"),
    pytest.param({"any": [{"field": "price", "op": "above", "value": 100}]},
                 id="any-price"),
    pytest.param({"all": [{"field": "rsi", "op": "below", "value": 30}]},
                 id="rsi-default-period"),
    pytest.param({"all": [{"field": "ma", "op": "below",
                           "params": {"period": 10}}]}, id="ma-default-value"),
    pytest.param({"all": [{"field": "ema_cross", "op": "below",
                           "params": {"fast": 2, "slow": 120}}]}, id="ema-death"),
    pytest.param({"all": [{"field": "window_low", "op": "below",
                           "params": {"minutes": 240}}]}, id="breakdown"),
]

INVALID_ENTRIES = [
    pytest.param("nope", id="entry-not-object"),
    pytest.param({}, id="no-group-key"),
    pytest.param({"all": [], "any": []}, id="both-group-keys"),
    pytest.param({"some": [{"field": "price", "op": "above", "value": 1}]},
                 id="unknown-group-key"),
    pytest.param({"all": "x"}, id="group-not-list"),
    pytest.param({"all": []}, id="empty-group"),
    pytest.param({"all": [{"field": "price", "op": "above", "value": 1}] * 6},
                 id="six-conditions"),
    pytest.param({"all": ["x"]}, id="condition-not-object"),
    pytest.param({"all": [{"field": "price", "op": "above", "value": 1, "x": 1}]},
                 id="extra-condition-key"),
    pytest.param({"all": [{"field": "vwap", "op": "above", "value": 1}]},
                 id="unknown-field"),
    pytest.param({"all": [{"field": "price", "op": "over", "value": 1}]},
                 id="unknown-op"),
    pytest.param({"all": [{"field": "window_low", "op": "above",
                           "params": {"minutes": 60}}]}, id="window-low-above-illegal"),
    pytest.param({"all": [{"field": "window_high", "op": "below",
                           "params": {"minutes": 60}}]}, id="window-high-below-illegal"),
    pytest.param({"all": [{"field": "price", "op": "above"}]}, id="price-missing-value"),
    pytest.param({"all": [{"field": "price", "op": "above", "value": 0}]},
                 id="price-value-not-positive"),
    pytest.param({"all": [{"field": "price", "op": "above", "value": "5"}]},
                 id="value-not-number"),
    pytest.param({"all": [{"field": "price", "op": "above", "value": True}]},
                 id="value-bool"),
    pytest.param({"all": [{"field": "day_change_pct", "op": "above"}]},
                 id="day-change-missing-value"),
    pytest.param({"all": [{"field": "rsi", "op": "above", "value": 101}]},
                 id="rsi-value-over-100"),
    pytest.param({"all": [{"field": "rsi", "op": "above", "value": -1}]},
                 id="rsi-value-negative"),
    pytest.param({"all": [{"field": "rsi", "op": "above", "value": 50,
                           "params": {"period": 51}}]}, id="rsi-period-high"),
    pytest.param({"all": [{"field": "rsi", "op": "above", "value": 50,
                           "params": {"period": 1}}]}, id="rsi-period-low"),
    pytest.param({"all": [{"field": "ma_cross", "op": "above", "value": 1,
                           "params": {"fast": 5, "slow": 20}}]}, id="cross-takes-no-value"),
    pytest.param({"all": [{"field": "ma_cross", "op": "above"}]},
                 id="cross-missing-params"),
    pytest.param({"all": [{"field": "ma_cross", "op": "above",
                           "params": {"fast": 20, "slow": 20}}]}, id="fast-not-below-slow"),
    pytest.param({"all": [{"field": "ma_cross", "op": "above",
                           "params": {"fast": 1, "slow": 20}}]}, id="cross-fast-low"),
    pytest.param({"all": [{"field": "ema_cross", "op": "above",
                           "params": {"fast": 5, "slow": 121}}]}, id="cross-slow-high"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0,
                           "params": {"period": 121}}]}, id="ma-period-high"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0,
                           "params": {"period": 1}}]}, id="ma-period-low"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0}]},
                 id="ma-missing-period"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0,
                           "params": {"period": 10.5}}]}, id="param-not-integer"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0,
                           "params": {"period": 10, "x": 1}}]}, id="extra-param-key"),
    pytest.param({"all": [{"field": "ma", "op": "above", "value": 0,
                           "params": "x"}]}, id="params-not-object"),
    pytest.param({"all": [{"field": "window_high", "op": "above",
                           "params": {"minutes": 4}}]}, id="minutes-low"),
    pytest.param({"all": [{"field": "window_low", "op": "below",
                           "params": {"minutes": 241}}]}, id="minutes-high"),
    pytest.param({"all": [{"field": "pullback_from_high_pct", "op": "above",
                           "params": {"minutes": 60}}]}, id="pullback-missing-value"),
    pytest.param({"all": [{"field": "pullback_from_high_pct", "op": "above",
                           "value": 0, "params": {"minutes": 60}}]},
                 id="pullback-value-not-positive"),
]


class TestValidateConditionGroup:
    @pytest.mark.parametrize("entry", VALID_ENTRIES)
    def test_valid(self, entry):
        assert validate_condition_group(entry) is None

    @pytest.mark.parametrize("entry", INVALID_ENTRIES)
    def test_invalid(self, entry):
        assert validate_condition_group(entry) is not None

    def test_registry_covers_contract_fields(self):
        assert set(FIELD_SPECS) == {
            "price", "day_change_pct", "ma", "ma_cross", "ema_cross", "rsi",
            "window_high", "window_low", "pullback_from_high_pct",
        }


class TestValidateExits:
    @pytest.mark.parametrize("exits", [
        pytest.param(None, id="none"),
        pytest.param({}, id="empty"),
        pytest.param({"take_profit_pct": 4, "stop_loss_pct": 3}, id="tp-sl"),
        pytest.param({"trailing_stop_pct": 2.5}, id="trailing"),
        pytest.param({"max_holding_days": 120}, id="max-holding"),
        pytest.param({"take_profit_pct": None, "stop_loss_pct": 3}, id="explicit-null"),
    ])
    def test_valid(self, exits):
        assert validate_exits(exits) is None

    @pytest.mark.parametrize("exits", [
        pytest.param("x", id="not-object"),
        pytest.param({"tp": 4}, id="unknown-key"),
        pytest.param({"take_profit_pct": 0}, id="tp-zero"),
        pytest.param({"stop_loss_pct": -1}, id="sl-negative"),
        pytest.param({"trailing_stop_pct": "2"}, id="trailing-not-number"),
        pytest.param({"max_holding_days": 0}, id="holding-zero"),
        pytest.param({"max_holding_days": 121}, id="holding-over"),
        pytest.param({"max_holding_days": 1.5}, id="holding-fractional"),
    ])
    def test_invalid(self, exits):
        assert validate_exits(exits) is not None

    def test_has_any_exit(self):
        assert has_any_exit({"take_profit_pct": 4})
        assert not has_any_exit({})
        assert not has_any_exit(None)
        assert not has_any_exit({"take_profit_pct": None})


class TestValidateSizing:
    @pytest.mark.parametrize("sizing", [
        pytest.param({"mode": "fixed_qty", "qty": 10}, id="fixed"),
        pytest.param({"mode": "fixed_qty", "qty": 0.5}, id="fractional"),
        pytest.param({"mode": "cash_pct", "pct": 1}, id="pct-min"),
        pytest.param({"mode": "cash_pct", "pct": 100}, id="pct-max"),
    ])
    def test_valid(self, sizing):
        assert validate_sizing(sizing) is None

    @pytest.mark.parametrize("sizing", [
        pytest.param(None, id="none"),
        pytest.param("x", id="not-object"),
        pytest.param({}, id="no-mode"),
        pytest.param({"mode": "market"}, id="unknown-mode"),
        pytest.param({"mode": "fixed_qty"}, id="missing-qty"),
        pytest.param({"mode": "fixed_qty", "qty": 0}, id="qty-zero"),
        pytest.param({"mode": "fixed_qty", "qty": 10, "pct": 5}, id="extra-key"),
        pytest.param({"mode": "cash_pct", "pct": 0}, id="pct-low"),
        pytest.param({"mode": "cash_pct", "pct": 101}, id="pct-high"),
        pytest.param({"mode": "cash_pct", "pct": True}, id="pct-bool"),
    ])
    def test_invalid(self, sizing):
        assert validate_sizing(sizing) is not None


class TestEvaluation:
    def test_price_inclusive_boundaries(self):
        bars = _bars([1.0])
        above = _group("price", "above", 100.0)
        below = _group("price", "below", 100.0)
        quote = {"price": 100.0, "day_change_percent": 0.0}
        assert evaluate_condition_group(above, bars, quote) is True
        assert evaluate_condition_group(below, bars, quote) is True
        assert evaluate_condition_group(above, bars, {"price": 99.99}) is False

    def test_day_change_and_attribute_quote(self):
        # PriceUpdate-like object: attributes instead of mapping keys.
        quote = SimpleNamespace(price=10.0, day_change_percent=-3.2)
        entry = _group("day_change_pct", "below", -3.0)
        assert evaluate_condition_group(entry, [], quote) is True
        assert evaluate_condition_group(
            _group("day_change_pct", "above", -3.0), [], quote
        ) is False

    def test_missing_quote_field_is_false(self):
        assert evaluate_condition_group(
            _group("day_change_pct", "above", 0), [], {"price": 10.0}
        ) is False

    def test_ma_offset_semantics(self):
        bars = _bars([10.0] * 10)
        entry = _group("ma", "above", 5, {"period": 10})  # price >= sma*1.05
        assert evaluate_condition_group(entry, bars, {"price": 10.5}) is True
        assert evaluate_condition_group(entry, bars, {"price": 10.4}) is False
        below = _group("ma", "below", 5, {"period": 10})  # price <= sma*0.95
        assert evaluate_condition_group(below, bars, {"price": 9.5}) is True
        assert evaluate_condition_group(below, bars, {"price": 9.6}) is False

    def test_ma_default_value_zero(self):
        bars = _bars([10.0] * 10)
        entry = _group("ma", "above", params={"period": 10})
        assert evaluate_condition_group(entry, bars, {"price": 10.0}) is True
        assert evaluate_condition_group(entry, bars, {"price": 9.99}) is False

    def test_golden_cross_fires_only_on_cross_minute(self):
        entry = _group("ma_cross", "above", params={"fast": 2, "slow": 3})
        # sma2: [None,10,10,15]; sma3: [None,None,10,13.33] -> cross at idx 3
        bars = _bars([10.0, 10.0, 10.0, 20.0])
        assert evaluate_condition_group(entry, bars, {"price": 20.0}) is True
        # One bar later (no new cross) the condition is False again.
        bars_after = _bars([10.0, 10.0, 10.0, 20.0, 21.0])
        assert evaluate_condition_group(entry, bars_after, {"price": 21.0}) is False

    def test_death_cross(self):
        entry = _group("ma_cross", "below", params={"fast": 2, "slow": 3})
        bars = _bars([10.0, 10.0, 10.0, 1.0])
        assert evaluate_condition_group(entry, bars, {"price": 1.0}) is True

    def test_ema_cross_uses_ema_series(self):
        entry = _group("ema_cross", "above", params={"fast": 2, "slow": 3})
        bars = _bars([10.0, 10.0, 10.0, 20.0])
        assert evaluate_condition_group(entry, bars, {"price": 20.0}) is True

    def test_window_breakout_and_breakdown(self):
        bars = _bars([9.0] * 5, highs=[10.0] * 5, lows=[8.0] * 5)
        breakout = _group("window_high", "above", params={"minutes": 5})
        assert evaluate_condition_group(breakout, bars, {"price": 10.0}) is True
        assert evaluate_condition_group(breakout, bars, {"price": 9.99}) is False
        breakdown = _group("window_low", "below", params={"minutes": 5})
        assert evaluate_condition_group(breakdown, bars, {"price": 8.0}) is True
        assert evaluate_condition_group(breakdown, bars, {"price": 8.01}) is False

    def test_pullback_from_high(self):
        bars = _bars([100.0] * 5, highs=[100.0] * 5)
        entry = _group("pullback_from_high_pct", "above", 2, {"minutes": 5})
        assert evaluate_condition_group(entry, bars, {"price": 97.0}) is True
        assert evaluate_condition_group(entry, bars, {"price": 99.0}) is False

    def test_rsi_condition(self):
        bars = _bars([10.0, 9.0, 8.0])  # straight down -> RSI 0
        entry = _group("rsi", "below", 30, {"period": 2})
        assert evaluate_condition_group(entry, bars, {"price": 8.0}) is True

    def test_all_vs_any(self):
        bars = _bars([10.0] * 10)
        conds = [
            {"field": "price", "op": "above", "value": 100.0},  # False at 50
            {"field": "day_change_pct", "op": "above", "value": 0.0},  # True
        ]
        quote = {"price": 50.0, "day_change_percent": 1.0}
        assert evaluate_condition_group({"all": conds}, bars, quote) is False
        assert evaluate_condition_group({"any": conds}, bars, quote) is True

    @pytest.mark.parametrize("entry", VALID_ENTRIES)
    def test_warmup_short_bars_is_false_and_never_raises(self, entry):
        quote = {"price": 10.0, "day_change_percent": 0.0}
        for bars in ([], _bars([10.0]), _bars([10.0, 10.0])):
            # Indicator fields lack warm-up -> False; pure quote fields may
            # legitimately evaluate — the call must simply never raise.
            evaluate_condition_group(entry, bars, quote)

    def test_indicator_false_at_negative_index(self):
        # evaluate_group_at with idx=-1 (no completed bars): indicator fields
        # are False — never Python-negative-indexed into the series tail.
        bars = _bars([10.0] * 10)
        entry = _group("ma", "above", 0, {"period": 2})
        ctx = build_series_context(entry, bars)
        assert evaluate_group_at(entry, ctx, -1, {"price": 100.0}) is False
        # A pure quote condition still works without any completed bars.
        price_entry = _group("price", "above", 1.0)
        assert evaluate_group_at(price_entry, {}, -1, {"price": 100.0}) is True

    def test_series_context_matches_point_functions(self):
        closes = [float(i) for i in range(1, 31)]
        bars = _bars(closes)
        entry = {"all": [
            {"field": "ma", "op": "above", "value": 0, "params": {"period": 10}},
            {"field": "rsi", "op": "above", "value": 50, "params": {"period": 14}},
        ]}
        ctx = build_series_context(entry, bars)
        assert ctx[("sma", 10)][-1] == sma(closes, 10)
        assert ctx[("rsi", 14)][-1] == rsi(closes, 14)
