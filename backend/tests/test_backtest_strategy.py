"""Tests for the P2 strategy path of the backtest engine (contract §4).

Covers:
- normalize_strategy_backtest_config: validation matrix (entry/exits/sizing/
  ticker/days/runs/seed), strategy_row input, CN board-lot fixed_qty check,
  universe params injection, config shape
- legacy equivalence: an adapted legacy trigger produces IDENTICAL numbers
  through the unified condition-group loop (stats/curves/trades)
- config echo: strategy shape for strategy runs; legacy keys never leak in
- crafted-bars engine semantics: trailing stop (priority SL -> trailing ->
  TP, high-water raised after checks, never on the entry bar),
  max_holding_days on synthetic days, cash_pct sizing (whole shares, CN
  board-lot floor, zero-share rejection), indicator warm-up, T+1 deferral

The legacy path itself is pinned byte-for-byte by tests/test_backtest_golden.py.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

import app.backtest as backtest
from app.backtest import (
    _simulate,
    normalize_backtest_config,
    normalize_strategy_backtest_config,
    run_backtest,
)
from app.market.cache import PriceCache
from app.market.profiles import CN_PROFILE, US_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import spread_bps_for

END_TIME = 10_000.0
HALF_SPREAD = spread_bps_for("TEST") / 2.0 / 10_000.0


def _crafted(highs, lows, closes, days=1):
    """Crafted bar set: opens mirror closes; times one minute apart."""
    n = len(closes)
    bars_per_day = n // days
    prev_closes = [float(closes[0])] + [
        float(closes[d * bars_per_day - 1]) for d in range(1, days)
    ]
    return {
        "times": np.arange(1_000, 1_000 + n * 60, 60, dtype=np.int64),
        "opens": np.array(closes, dtype=float),
        "highs": np.array(highs, dtype=float),
        "lows": np.array(lows, dtype=float),
        "closes": np.array(closes, dtype=float),
        "prev_closes": prev_closes,
    }


def _install_bars(monkeypatch, bars, bars_per_day):
    monkeypatch.setattr(backtest, "BARS_PER_DAY", bars_per_day)
    monkeypatch.setattr(
        backtest, "_generate_bars", lambda *a, **k: {k_: v for k_, v in bars.items()}
    )


ALWAYS_ENTRY = {"all": [{"field": "price", "op": "above", "value": 1.0}]}


def _strategy_config(**overrides):
    config = {
        "ticker": "TEST",
        "entry": ALWAYS_ENTRY,
        "exits": {},
        "sizing": {"mode": "fixed_qty", "qty": 1.0},
        "days": 1,
        "runs": 1,
        "seed": 0,
        "anchor_price": 100.0,
        "source": "strategy",
    }
    config.update(overrides)
    return config


def _normalize_ok(**fields):
    defaults = dict(
        ticker="AAPL",
        entry=ALWAYS_ENTRY,
        exits=None,
        sizing={"mode": "fixed_qty", "qty": 10},
        seed=1,
    )
    defaults.update(fields)
    return normalize_strategy_backtest_config(PriceCache(), **defaults)


class TestNormalizeStrategyConfig:
    def test_ok_config_shape(self):
        outcome = _normalize_ok(days=10, runs=2)
        assert outcome["status"] == "ok"
        config = outcome["config"]
        assert set(config) == {
            "ticker", "entry", "exits", "sizing", "days", "runs", "seed",
            "anchor_price", "source",
        }
        assert config["source"] == "strategy"
        assert config["ticker"] == "AAPL"
        assert config["anchor_price"] == SEED_PRICES["AAPL"]
        assert config["exits"] == {}
        assert config["sizing"] == {"mode": "fixed_qty", "qty": 10.0}

    def test_anchor_prefers_live_cache(self):
        cache = PriceCache()
        cache.update("AAPL", 500.0)
        outcome = normalize_strategy_backtest_config(
            cache, ticker=" aapl ", entry=ALWAYS_ENTRY, exits=None,
            sizing={"mode": "fixed_qty", "qty": 1}, seed=1,
        )
        assert outcome["config"]["anchor_price"] == 500.0

    def test_unknown_ticker(self):
        outcome = _normalize_ok(ticker="ZZZZ")
        assert outcome == {"status": "failed", "ticker": "ZZZZ", "error": "Ticker not found"}

    def test_invalid_entry(self):
        outcome = _normalize_ok(entry={"all": []})
        assert outcome["status"] == "failed"
        assert outcome["error"].startswith("entry:")

    def test_invalid_exits(self):
        outcome = _normalize_ok(exits={"take_profit_pct": -1})
        assert outcome["status"] == "failed"
        assert outcome["error"].startswith("exits:")

    def test_invalid_sizing(self):
        outcome = _normalize_ok(sizing={"mode": "cash_pct", "pct": 0})
        assert outcome["status"] == "failed"
        assert outcome["error"].startswith("sizing:")

    def test_cn_fixed_qty_must_be_whole_lots(self):
        outcome = _normalize_ok(
            ticker="600036",
            sizing={"mode": "fixed_qty", "qty": 150},
            universe=CN_PROFILE.universe,
            profile=CN_PROFILE,
        )
        assert outcome["status"] == "failed"
        assert outcome["error"] == "A股买入须为 100 股的整数倍"

    def test_cn_cash_pct_needs_no_upfront_lot_check(self):
        outcome = _normalize_ok(
            ticker="600036",
            sizing={"mode": "cash_pct", "pct": 20},
            universe=CN_PROFILE.universe,
            profile=CN_PROFILE,
        )
        assert outcome["status"] == "ok"
        assert outcome["config"]["params"] == CN_PROFILE.universe.ticker_params["600036"]

    def test_strategy_row_with_json_text_columns(self):
        row = {
            "ticker": "MSFT",
            "entry": '{"all": [{"field": "day_change_pct", "op": "below", "value": -2}]}',
            "exits": '{"take_profit_pct": 4, "stop_loss_pct": 3}',
            "sizing": '{"mode": "cash_pct", "pct": 25}',
        }
        outcome = normalize_strategy_backtest_config(
            PriceCache(), strategy_row=row, days=10, seed=7
        )
        assert outcome["status"] == "ok"
        config = outcome["config"]
        assert config["ticker"] == "MSFT"
        assert config["entry"] == {
            "all": [{"field": "day_change_pct", "op": "below", "value": -2}]
        }
        assert config["exits"] == {"take_profit_pct": 4, "stop_loss_pct": 3}
        assert config["sizing"] == {"mode": "cash_pct", "pct": 25.0}

    def test_none_exit_values_are_dropped(self):
        outcome = _normalize_ok(exits={"take_profit_pct": 5, "stop_loss_pct": None})
        assert outcome["config"]["exits"] == {"take_profit_pct": 5}

    @pytest.mark.parametrize("field,value,message", [
        ("days", 4, "days must be between 5 and 120"),
        ("days", 121, "days must be between 5 and 120"),
        ("runs", 0, "runs must be between 1 and 50"),
        ("runs", 51, "runs must be between 1 and 50"),
        ("seed", -1, "seed must be a non-negative integer"),
    ])
    def test_bounds(self, field, value, message):
        outcome = _normalize_ok(**{field: value})
        assert outcome == {"status": "failed", "ticker": "AAPL", "error": message}

    def test_seed_drawn_when_omitted(self):
        outcome = _normalize_ok(seed=None)
        assert outcome["status"] == "ok"
        assert outcome["config"]["seed"] >= 0


class TestLegacyEquivalence:
    def test_adapted_trigger_matches_legacy_numbers(self):
        legacy = normalize_backtest_config(
            PriceCache(), ticker="AAPL", trigger_type="day_change_pct_below",
            threshold=-1.0, quantity=10, take_profit_pct=3.0, stop_loss_pct=2.0,
            days=10, seed=777,
        )["config"]
        strategy = normalize_strategy_backtest_config(
            PriceCache(), ticker="AAPL",
            entry={"all": [{"field": "day_change_pct", "op": "below", "value": -1.0}]},
            exits={"take_profit_pct": 3.0, "stop_loss_pct": 2.0},
            sizing={"mode": "fixed_qty", "qty": 10},
            days=10, seed=777,
        )["config"]

        legacy_res = run_backtest(legacy, commission_bps=0.0, end_time=END_TIME)
        strategy_res = run_backtest(strategy, commission_bps=0.0, end_time=END_TIME)
        assert strategy_res["stats"] == legacy_res["stats"]
        assert strategy_res["equity_curve"] == legacy_res["equity_curve"]
        assert strategy_res["baseline_curve"] == legacy_res["baseline_curve"]
        assert strategy_res["trades"] == legacy_res["trades"]
        assert legacy_res["stats"]["fires"] >= 1  # non-degenerate comparison

    def test_strategy_config_echo_shape(self):
        config = _normalize_ok(days=5)["config"]
        result = run_backtest(config, commission_bps=2.5, end_time=END_TIME)
        echo = result["config"]
        assert set(echo) == {
            "ticker", "entry", "exits", "sizing", "days", "runs", "seed",
            "commission_bps", "anchor_price", "source",
        }
        assert echo["source"] == "strategy"
        assert echo["entry"] == ALWAYS_ENTRY
        assert echo["commission_bps"] == 2.5
        # Legacy keys never appear in a strategy echo (and vice versa —
        # goldens pin the legacy echo).
        for legacy_key in ("trigger_type", "threshold", "quantity", "side",
                           "take_profit_pct", "stop_loss_pct"):
            assert legacy_key not in echo

    def test_runs_summary_on_strategy_path(self):
        config = _normalize_ok(days=5, runs=3)["config"]
        result = run_backtest(config, commission_bps=0.0, end_time=END_TIME)
        assert result["runs_summary"] is not None
        assert result["runs_summary"]["runs"] == 3
        assert set(result) == {
            "config", "stats", "equity_curve", "baseline_curve", "trades",
            "runs_summary",
        }

    def test_stats_key_set_unchanged_on_strategy_path(self):
        legacy = normalize_backtest_config(
            PriceCache(), ticker="AAPL", trigger_type="price_above",
            threshold=1.0, quantity=1, days=5, seed=1,
        )["config"]
        strategy = _normalize_ok(days=5)["config"]
        legacy_stats = run_backtest(legacy, commission_bps=0.0, end_time=END_TIME)["stats"]
        strategy_stats = run_backtest(strategy, commission_bps=0.0, end_time=END_TIME)["stats"]
        assert set(strategy_stats) == set(legacy_stats)


def _sells(result):
    return [t for t in result["trades"] if t["side"] == "sell"]


class TestTrailingStop:
    def test_trailing_stop_fires_from_raised_high_water(self, monkeypatch):
        # Entry at bar 0 close (100). Bar 1 rallies (high 112) — no exit,
        # high-water rises to 112. Bar 2's low 106 <= 112*0.95=106.4 ->
        # trailing exit at 106.4.
        bars = _crafted(
            highs=[100.0, 112.0, 112.0, 112.0],
            lows=[100.0, 108.0, 106.0, 106.0],
            closes=[100.0, 110.0, 109.0, 108.0],
        )
        _install_bars(monkeypatch, bars, bars_per_day=4)
        config = _strategy_config(exits={"trailing_stop_pct": 5.0})
        res = _simulate(config, 0, 0.0, END_TIME)
        sells = [t for t in res["trades"] if t["side"] == "sell"]
        assert sells[0]["reason"] == "trailing_stop"
        assert sells[0]["time"] == 1_120  # bar 2
        expected_level = 112.0 * 0.95
        assert sells[0]["price"] == round(expected_level * (1.0 - HALF_SPREAD), 2)

    def test_stop_loss_beats_trailing_on_double_hit(self, monkeypatch):
        # Bar 1 low 90 breaches both the 3% stop (~97) and the 5% trail
        # (~95) — the stop wins (priority SL -> trailing -> TP).
        bars = _crafted(
            highs=[100.0, 100.0],
            lows=[100.0, 90.0],
            closes=[100.0, 95.0],
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(
            exits={"stop_loss_pct": 3.0, "trailing_stop_pct": 5.0}
        )
        res = _simulate(config, 0, 0.0, END_TIME)
        assert _sells(res)[0]["reason"] == "stop_loss"

    def test_trailing_beats_take_profit_on_double_hit(self, monkeypatch):
        # Bar 1 touches both the TP (high 120 >= ~105) and the trail
        # (low 94 <= ~95) — trailing is checked first.
        bars = _crafted(
            highs=[100.0, 120.0],
            lows=[100.0, 94.0],
            closes=[100.0, 100.0],
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(
            exits={"take_profit_pct": 5.0, "trailing_stop_pct": 5.0}
        )
        res = _simulate(config, 0, 0.0, END_TIME)
        assert _sells(res)[0]["reason"] == "trailing_stop"

    def test_entry_bar_high_does_not_seed_high_water(self, monkeypatch):
        # Bar 0's own high (150, before the close-fill entry) must NOT raise
        # the high-water above the entry fill: bar 1 low 96 stays above the
        # ~95 trail from the ~100 entry, so no exit fires.
        bars = _crafted(
            highs=[150.0, 100.0],
            lows=[100.0, 96.0],
            closes=[100.0, 100.0],
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(exits={"trailing_stop_pct": 5.0})
        res = _simulate(config, 0, 0.0, END_TIME)
        # Only the horizon-end close — no trailing_stop sell.
        assert [t["reason"] for t in _sells(res)] == ["horizon_end"]


class TestMaxHoldingDays:
    def test_exits_on_first_bar_of_limit_day(self, monkeypatch):
        bars = _crafted(
            highs=[100.0] * 6,
            lows=[100.0] * 6,
            closes=[100.0] * 6,
            days=3,
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(exits={"max_holding_days": 1}, days=3)
        res = _simulate(config, 0, 0.0, END_TIME)
        first_sell = _sells(res)[0]
        assert first_sell["reason"] == "max_holding_days"
        assert first_sell["time"] == 1_000 + 2 * 60  # first bar of day 1

    def test_respects_t1_with_same_day_semantics(self, monkeypatch):
        # T+1 never conflicts with max_holding_days >= 1 (both defer to the
        # next synthetic day); the exit still lands on day 1's first bar.
        t1_only = replace(
            US_PROFILE, t_plus=1, min_commission=0.0, stamp_tax_bps_sell=0.0
        )
        bars = _crafted(
            highs=[100.0] * 4,
            lows=[100.0] * 4,
            closes=[100.0] * 4,
            days=2,
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(exits={"max_holding_days": 1}, days=2)
        res = _simulate(config, 0, 0.0, END_TIME, profile=t1_only)
        first_sell = _sells(res)[0]
        assert first_sell["reason"] == "max_holding_days"
        assert first_sell["time"] == 1_000 + 2 * 60


class TestT1StrategyPath:
    def test_no_exit_on_entry_day_under_t1(self, monkeypatch):
        # TP reachable from bar 1 on; under T+1 the same-day (bar 1) hit is
        # skipped and the exit lands on day 1 (bar 2).
        bars = _crafted(
            highs=[100.0, 110.0, 110.0, 110.0],
            lows=[100.0] * 4,
            closes=[100.0] * 4,
            days=2,
        )
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(exits={"take_profit_pct": 5.0}, days=2)

        no_t1 = _simulate(config, 0, 0.0, END_TIME, profile=None)
        assert _sells(no_t1)[0]["time"] == 1_060  # bar 1, same day

        t1_only = replace(
            US_PROFILE, t_plus=1, min_commission=0.0, stamp_tax_bps_sell=0.0
        )
        with_t1 = _simulate(config, 0, 0.0, END_TIME, profile=t1_only)
        assert _sells(with_t1)[0]["time"] == 1_120  # first bar of day 1


class TestCashPctSizing:
    def test_whole_shares_of_current_cash(self, monkeypatch):
        bars = _crafted(highs=[100.0] * 2, lows=[100.0] * 2, closes=[100.0] * 2)
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(sizing={"mode": "cash_pct", "pct": 50.0})
        res = _simulate(config, 0, 0.0, END_TIME)  # starting cash 10_000
        buy = res["trades"][0]
        buy_px = 100.0 * (1.0 + HALF_SPREAD)
        assert buy["quantity"] == float(int(5_000.0 / buy_px))  # 49 whole shares
        assert res["stats"]["fires"] == 1

    def test_cn_floors_to_whole_board_lots(self, monkeypatch):
        bars = _crafted(highs=[100.0] * 2, lows=[100.0] * 2, closes=[100.0] * 2)
        _install_bars(monkeypatch, bars, bars_per_day=2)
        lot_profile = replace(
            US_PROFILE, lot_size=100, min_commission=0.0, stamp_tax_bps_sell=0.0
        )
        config = _strategy_config(sizing={"mode": "cash_pct", "pct": 100.0})
        res = _simulate(config, 0, 0.0, END_TIME, 100_000.0, lot_profile)
        # floor(100000/buy_px) = 999 shares -> floored to 9 lots = 900.
        assert res["trades"][0]["quantity"] == 900.0

    def test_zero_share_result_is_insufficient_cash_rejection(self, monkeypatch):
        bars = _crafted(highs=[100.0] * 2, lows=[100.0] * 2, closes=[100.0] * 2)
        _install_bars(monkeypatch, bars, bars_per_day=2)
        config = _strategy_config(sizing={"mode": "cash_pct", "pct": 1.0})
        res = _simulate(config, 0, 0.0, END_TIME, 5_000.0)  # 1% = $50 < 1 share
        assert res["trades"] == []
        assert res["stats"]["fires"] == 0
        assert res["stats"]["rejections"]["insufficient_cash"] == 1  # one per day


class TestIndicatorEntries:
    def test_window_high_breakout_entry(self, monkeypatch):
        # Rolling high of the 5 completed bars before bar 5 is 10.0; bar 5
        # closes at 11 -> breakout fires there (not earlier: warm-up).
        closes = [9.0, 9.0, 9.0, 9.0, 9.0, 11.0, 11.0, 11.0]
        bars = _crafted(highs=[10.0] * 5 + [11.0] * 3, lows=[8.0] * 8, closes=closes)
        _install_bars(monkeypatch, bars, bars_per_day=8)
        config = _strategy_config(
            entry={"all": [{"field": "window_high", "op": "above",
                            "params": {"minutes": 5}}]},
        )
        res = _simulate(config, 0, 0.0, END_TIME)
        buys = [t for t in res["trades"] if t["side"] == "buy"]
        assert len(buys) == 1
        assert buys[0]["time"] == 1_000 + 5 * 60

    def test_ma_entry_uses_completed_bars_only(self, monkeypatch):
        # SMA(3) needs 3 COMPLETED bars -> earliest fire is bar 3.
        closes = [10.0, 10.0, 10.0, 20.0]
        bars = _crafted(highs=closes, lows=closes, closes=closes)
        _install_bars(monkeypatch, bars, bars_per_day=4)
        config = _strategy_config(
            entry={"all": [{"field": "ma", "op": "above", "value": 0,
                            "params": {"period": 3}}]},
        )
        res = _simulate(config, 0, 0.0, END_TIME)
        buys = [t for t in res["trades"] if t["side"] == "buy"]
        assert len(buys) == 1
        assert buys[0]["time"] == 1_000 + 3 * 60

    def test_warmup_shortfall_never_fires_never_raises(self, monkeypatch):
        closes = [10.0] * 8
        bars = _crafted(highs=closes, lows=closes, closes=closes)
        _install_bars(monkeypatch, bars, bars_per_day=8)
        config = _strategy_config(
            entry={"all": [{"field": "rsi", "op": "below", "value": 100,
                            "params": {"period": 14}}]},
        )
        res = _simulate(config, 0, 0.0, END_TIME)
        assert res["stats"]["fires"] == 0
        assert res["trades"] == []

    def test_indicator_entry_through_run_backtest_real_bars(self):
        # End-to-end on real GBM bars: an RSI dip-buyer with tp/sl completes
        # and reports the standard stats block.
        outcome = normalize_strategy_backtest_config(
            PriceCache(),
            ticker="TSLA",
            entry={"all": [{"field": "rsi", "op": "below", "value": 45,
                            "params": {"period": 14}}]},
            exits={"take_profit_pct": 3.0, "stop_loss_pct": 2.0},
            sizing={"mode": "cash_pct", "pct": 50},
            days=10,
            seed=99,
        )
        assert outcome["status"] == "ok"
        result = run_backtest(outcome["config"], commission_bps=0.0, end_time=END_TIME)
        assert result["stats"]["fires"] >= 1
        assert result["config"]["source"] == "strategy"
        assert 1 <= len(result["equity_curve"]) <= 400
