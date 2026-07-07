"""Tests for the M5 strategy backtester: engine (app.backtest) and POST /api/backtest.

Engine:
- determinism per seed, divergence across seeds
- config normalization (anchor resolution, seed drawing, failure dicts)
- trigger semantics for all four trigger types (rules-engine parity)
- take-profit / stop-loss exits, same-bar conservatism (stop-loss wins),
  horizon-end close-out and round-trip accounting
- insufficient-cash rejection counting; spread + commission on both legs
- win_rate / avg_win / avg_loss / profit_factor null semantics
- curve downsampling (<= 400 points, strictly ascending times, last point kept)
- Monte Carlo: runs_summary aggregation, lower-middle-median representative

Route:
- 200 happy-path shape, defaults, seed echo/drawing, 400 validation matrix
"""

from __future__ import annotations

import numpy as np
import pytest

from app.backtest import normalize_backtest_config, run_backtest
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import spread_bps_for

END_TIME = 1_750_000_000.0
# Final bar of the last simulated day: end_time - 1 day + 389 minutes.
LAST_BAR_TIME = int(END_TIME) - 86_400 + 389 * 60

RESPONSE_KEYS = {
    "config", "stats", "equity_curve", "baseline_curve", "trades", "runs_summary",
}
CONFIG_KEYS = {
    "ticker", "trigger_type", "threshold", "side", "quantity", "take_profit_pct",
    "stop_loss_pct", "days", "runs", "seed", "commission_bps", "anchor_price",
}
STATS_KEYS = {
    "total_return_pct", "buy_hold_return_pct", "max_drawdown_pct", "final_equity",
    "fires", "round_trips", "win_rate", "avg_win", "avg_loss", "profit_factor",
    "commission_paid", "rejections",
}
RUNS_SUMMARY_KEYS = {
    "runs", "median_return_pct", "p05_return_pct", "p95_return_pct",
    "positive_share", "median_max_drawdown_pct",
}

NVDA_HALF_SPREAD = spread_bps_for("NVDA") / 2.0 / 10_000.0


def _config(**overrides) -> dict:
    """Normalized engine config with sensible defaults (via the shared helper).

    The default trigger (price_above $1, far below the $800 NVDA anchor)
    provably fires on the first bar of day 1; with no exits configured the
    position rides to the horizon.
    """
    fields: dict = {
        "ticker": "NVDA",
        "trigger_type": "price_above",
        "threshold": 1.0,
        "quantity": 5,
        "take_profit_pct": None,
        "stop_loss_pct": None,
        "days": 5,
        "runs": 1,
        "seed": 42,
    }
    fields.update(overrides)
    outcome = normalize_backtest_config(PriceCache(), **fields)
    assert outcome["status"] == "ok", outcome
    return outcome["config"]


def _fake_bars_factory(bars: dict):
    """Stand-in for app.backtest._generate_bars returning a crafted path."""

    def fake_generate(ticker, anchor_price, days, seed, end_time):
        return bars

    return fake_generate


def _crafted_bars(highs: list[float], lows: list[float], closes: list[float]) -> dict:
    """Minimal single-day bar set (opens mirror closes; times one minute apart)."""
    n = len(closes)
    return {
        "times": np.arange(1_000, 1_000 + n * 60, 60, dtype=np.int64),
        "opens": np.array(closes, dtype=float),
        "highs": np.array(highs, dtype=float),
        "lows": np.array(lows, dtype=float),
        "closes": np.array(closes, dtype=float),
        "prev_closes": [float(closes[0])],
    }


class TestNormalizeConfig:
    def test_anchor_prefers_live_cache_quote(self):
        cache = PriceCache()
        cache.update("NVDA", 900.0)
        outcome = normalize_backtest_config(
            cache, ticker=" nvda ", trigger_type="PRICE_ABOVE", threshold=1.0, quantity=1
        )
        assert outcome["status"] == "ok"
        config = outcome["config"]
        assert config["ticker"] == "NVDA"
        assert config["trigger_type"] == "price_above"
        assert config["anchor_price"] == 900.0

    def test_anchor_falls_back_to_seed_prices(self):
        outcome = normalize_backtest_config(
            PriceCache(), ticker="NVDA", trigger_type="price_above", threshold=1.0, quantity=1
        )
        assert outcome["config"]["anchor_price"] == SEED_PRICES["NVDA"]

    def test_unknown_ticker_fails(self):
        outcome = normalize_backtest_config(
            PriceCache(), ticker="ZZZZ", trigger_type="price_above", threshold=1.0, quantity=1
        )
        assert outcome == {"status": "failed", "ticker": "ZZZZ", "error": "Ticker not found"}

    def test_defaults_applied(self):
        config = normalize_backtest_config(
            PriceCache(), ticker="NVDA", trigger_type="price_above", threshold=1.0, quantity=1
        )["config"]
        assert config["side"] == "buy"
        assert config["days"] == 30
        assert config["runs"] == 1
        assert config["take_profit_pct"] is None
        assert config["stop_loss_pct"] is None

    def test_seed_drawn_when_omitted(self):
        config = normalize_backtest_config(
            PriceCache(), ticker="NVDA", trigger_type="price_above", threshold=1.0, quantity=1
        )["config"]
        assert isinstance(config["seed"], int)
        assert config["seed"] >= 0


class TestEngineDeterminism:
    def test_same_seed_identical_result(self):
        cfg = _config(take_profit_pct=2.0, stop_loss_pct=2.0)
        first = run_backtest(cfg, commission_bps=5.0, end_time=END_TIME)
        second = run_backtest(cfg, commission_bps=5.0, end_time=END_TIME)
        assert first == second

    def test_different_seeds_differ(self):
        first = run_backtest(_config(seed=1), end_time=END_TIME)
        second = run_backtest(_config(seed=2), end_time=END_TIME)
        assert first["equity_curve"] != second["equity_curve"]
        assert first["baseline_curve"] != second["baseline_curve"]


class TestTriggerSemantics:
    @pytest.mark.parametrize(
        ("trigger_type", "threshold"),
        [
            pytest.param("price_above", 1.0, id="price-above"),
            pytest.param("price_below", 1_000_000.0, id="price-below"),
            pytest.param("day_change_pct_above", -100.0, id="day-change-above"),
            pytest.param("day_change_pct_below", 100.0, id="day-change-below"),
        ],
    )
    def test_always_true_trigger_fires_on_first_bar(self, trigger_type, threshold):
        result = run_backtest(
            _config(trigger_type=trigger_type, threshold=threshold), end_time=END_TIME
        )
        # No exits configured -> one entry on day 1 held to the horizon.
        assert result["stats"]["fires"] == 1
        buy = result["trades"][0]
        assert buy["side"] == "buy"
        assert buy["reason"] == "trigger"
        assert buy["pnl"] is None
        assert buy["time"] == int(END_TIME) - 5 * 86_400  # first bar of day 1

    @pytest.mark.parametrize(
        ("trigger_type", "threshold"),
        [
            pytest.param("price_above", 1_000_000.0, id="price-above"),
            pytest.param("price_below", 0.01, id="price-below"),
            pytest.param("day_change_pct_above", 1_000.0, id="day-change-above"),
            pytest.param("day_change_pct_below", -1_000.0, id="day-change-below"),
        ],
    )
    def test_never_true_trigger_never_fires(self, trigger_type, threshold):
        result = run_backtest(
            _config(trigger_type=trigger_type, threshold=threshold), end_time=END_TIME
        )
        stats = result["stats"]
        assert stats["fires"] == 0
        assert stats["round_trips"] == 0
        assert result["trades"] == []
        assert stats["final_equity"] == 10_000.0
        assert stats["total_return_pct"] == 0.0


class TestExits:
    def test_take_profit_exit(self):
        # seed=2: a rising path with many re-armed entries and TP exits
        # (seed 42 dives ~15% on day 1 and never tags a 1% TP again).
        result = run_backtest(
            _config(take_profit_pct=1.0, days=30, seed=2), end_time=END_TIME
        )
        tp_sells = [t for t in result["trades"] if t["reason"] == "take_profit"]
        assert tp_sells, "expected at least one take-profit exit over 30 days"
        # A 1% TP comfortably clears the <=5bp round-trip spread (commission 0)
        assert all(t["pnl"] > 0 for t in tp_sells)

    def test_stop_loss_exit(self):
        result = run_backtest(_config(stop_loss_pct=1.0, days=30), end_time=END_TIME)
        sl_sells = [t for t in result["trades"] if t["reason"] == "stop_loss"]
        assert sl_sells, "expected at least one stop-loss exit over 30 days"
        assert all(t["pnl"] < 0 for t in sl_sells)

    def test_same_bar_double_hit_is_a_stop(self, monkeypatch):
        import app.backtest as backtest_module

        # Bar 0: close 100 -> entry (price_above $1 fires). Bar 1: low tags
        # the 3% stop AND high tags the 5% take-profit -> conservative fill
        # is the stop. Bar 2: flat (fired_today blocks re-entry).
        crafted = _crafted_bars(
            highs=[100.0, 120.0, 100.0],
            lows=[100.0, 80.0, 100.0],
            closes=[100.0, 100.0, 100.0],
        )
        monkeypatch.setattr(backtest_module, "_generate_bars", _fake_bars_factory(crafted))

        result = run_backtest(
            _config(take_profit_pct=5.0, stop_loss_pct=3.0), end_time=END_TIME
        )

        buy, sell = result["trades"]
        assert sell["reason"] == "stop_loss"
        # Exact fill math: spread applied on both legs
        buy_px = 100.0 * (1.0 + NVDA_HALF_SPREAD)
        assert buy["price"] == round(buy_px, 2)
        sl_level = buy_px * (1.0 - 0.03)
        assert sell["price"] == round(sl_level * (1.0 - NVDA_HALF_SPREAD), 2)
        assert sell["pnl"] < 0
        # Short crafted path: no downsampling, one point per bar
        assert len(result["equity_curve"]) == 3

    def test_profit_factor_null_when_no_losses(self, monkeypatch):
        import app.backtest as backtest_module

        # Bar 0: entry at 100. Bar 1: high tags the 5% take-profit, low never
        # nears a stop -> a single winning round trip and nothing else.
        crafted = _crafted_bars(
            highs=[100.0, 120.0, 100.0],
            lows=[100.0, 99.0, 100.0],
            closes=[100.0, 100.0, 100.0],
        )
        monkeypatch.setattr(backtest_module, "_generate_bars", _fake_bars_factory(crafted))

        result = run_backtest(
            _config(take_profit_pct=5.0, stop_loss_pct=50.0), end_time=END_TIME
        )
        stats = result["stats"]
        assert stats["round_trips"] == 1
        assert stats["win_rate"] == 1.0
        assert stats["avg_win"] > 0
        assert stats["avg_loss"] is None
        assert stats["profit_factor"] is None  # gross losses == 0


class TestHorizonEnd:
    def test_open_position_closes_at_final_bar(self):
        result = run_backtest(_config(), end_time=END_TIME)  # no exits configured
        stats = result["stats"]
        buy, sell = result["trades"]
        assert (buy["side"], sell["side"]) == ("buy", "sell")
        assert sell["reason"] == "horizon_end"
        assert sell["time"] == LAST_BAR_TIME
        assert sell["pnl"] is not None
        # The close-out counts as a round trip
        assert stats["fires"] == 1
        assert stats["round_trips"] == 1
        assert stats["win_rate"] in (0.0, 1.0)
        # Round-trip accounting: final equity = $10k + the single trip's pnl
        assert stats["final_equity"] == pytest.approx(10_000.0 + sell["pnl"], abs=0.02)
        # The equity curve lands exactly on the realized final equity
        assert result["equity_curve"][-1]["value"] == stats["final_equity"]


class TestRejections:
    def test_insufficient_cash_consumes_the_days_fire(self):
        # ~$800M notional vs $10k cash -> every daily fire is rejected.
        result = run_backtest(_config(quantity=1_000_000), end_time=END_TIME)
        stats = result["stats"]
        assert stats["rejections"] == {"insufficient_cash": 5}  # once per day (days=5)
        assert stats["fires"] == 0
        assert result["trades"] == []
        assert stats["final_equity"] == 10_000.0


class TestFrictions:
    def test_commission_charged_on_both_legs(self, monkeypatch):
        import app.backtest as backtest_module

        # Flat crafted path: entry at 100 on bar 0, horizon close-out at 100.
        crafted = _crafted_bars(
            highs=[100.0, 100.0, 100.0],
            lows=[100.0, 100.0, 100.0],
            closes=[100.0, 100.0, 100.0],
        )
        monkeypatch.setattr(backtest_module, "_generate_bars", _fake_bars_factory(crafted))

        result = run_backtest(_config(), commission_bps=25.0, end_time=END_TIME)
        buy, sell = result["trades"]
        assert sell["reason"] == "horizon_end"
        # 25bp on both legs; the +/- half-spread notionals cancel exactly:
        # (5*100*(1+hs) + 5*100*(1-hs)) * 0.0025 = 2.50
        assert result["stats"]["commission_paid"] == pytest.approx(2.50, abs=0.01)
        # Spread on both legs: buy above the close, sell below it
        assert buy["price"] == round(100.0 * (1.0 + NVDA_HALF_SPREAD), 2)
        assert sell["price"] == round(100.0 * (1.0 - NVDA_HALF_SPREAD), 2)

    def test_commission_reduces_final_equity(self):
        free = run_backtest(_config(), commission_bps=0.0, end_time=END_TIME)
        paid = run_backtest(_config(), commission_bps=10.0, end_time=END_TIME)
        assert free["stats"]["commission_paid"] == 0.0
        assert paid["stats"]["commission_paid"] > 0
        assert paid["stats"]["final_equity"] < free["stats"]["final_equity"]


class TestCurveDownsampling:
    def test_long_run_capped_at_400_points_keeping_last(self):
        result = run_backtest(_config(days=30), end_time=END_TIME)  # 11,700 bars
        for curve in (result["equity_curve"], result["baseline_curve"]):
            assert len(curve) <= 400
            times = [p["time"] for p in curve]
            assert times == sorted(times)
            assert len(set(times)) == len(times)  # strictly ascending
            assert times[-1] == LAST_BAR_TIME
        # Baseline: $10k fully invested at the first bar close, frictionless
        assert result["baseline_curve"][0]["value"] == 10_000.0

    def test_curves_share_timestamps(self):
        result = run_backtest(_config(days=10), end_time=END_TIME)
        assert [p["time"] for p in result["equity_curve"]] == [
            p["time"] for p in result["baseline_curve"]
        ]


class TestMonteCarlo:
    def test_runs_summary_populated_and_ordered(self):
        cfg = _config(take_profit_pct=2.0, stop_loss_pct=2.0, days=10, runs=7, seed=100)
        result = run_backtest(cfg, end_time=END_TIME)
        summary = result["runs_summary"]
        assert set(summary.keys()) == RUNS_SUMMARY_KEYS
        assert summary["runs"] == 7
        assert (
            summary["p05_return_pct"]
            <= summary["median_return_pct"]
            <= summary["p95_return_pct"]
        )
        assert 0.0 <= summary["positive_share"] <= 1.0
        assert summary["median_max_drawdown_pct"] >= 0.0

    def test_single_run_summary_null(self):
        result = run_backtest(_config(), end_time=END_TIME)
        assert result["runs_summary"] is None

    def test_representative_is_lower_middle_median_run(self):
        # Even N: the representative run is the lower-middle by return.
        multi = run_backtest(
            _config(take_profit_pct=2.0, stop_loss_pct=2.0, days=10, runs=4, seed=7),
            end_time=END_TIME,
        )
        singles = [
            run_backtest(
                _config(take_profit_pct=2.0, stop_loss_pct=2.0, days=10, seed=7 + i),
                end_time=END_TIME,
            )["stats"]["total_return_pct"]
            for i in range(4)
        ]
        assert multi["stats"]["total_return_pct"] == sorted(singles)[1]


# ---------------------------------------------------------------------------
# POST /api/backtest route
# ---------------------------------------------------------------------------


BODY = {
    "ticker": "NVDA",
    "trigger_type": "day_change_pct_below",
    "threshold": -3.0,
    "side": "buy",
    "quantity": 5,
    "take_profit_pct": 5.0,
    "stop_loss_pct": 3.0,
    "days": 10,
    "runs": 1,
    "seed": 42,
}


@pytest.mark.asyncio
class TestBacktestRoute:
    async def test_happy_path_shape(self, app_client):
        resp = await app_client.post("/api/backtest", json=BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == RESPONSE_KEYS
        config = data["config"]
        assert set(config.keys()) == CONFIG_KEYS
        assert config["ticker"] == "NVDA"
        assert config["side"] == "buy"
        assert config["seed"] == 42  # echoed
        assert config["commission_bps"] == 0.0
        assert config["anchor_price"] == SEED_PRICES["NVDA"]  # cache seeded with SEED_PRICES
        assert set(data["stats"].keys()) == STATS_KEYS
        assert data["stats"]["rejections"] == {"insufficient_cash": 0}
        assert data["runs_summary"] is None
        assert 0 < len(data["equity_curve"]) <= 400
        assert len(data["baseline_curve"]) == len(data["equity_curve"])

    async def test_defaults_and_drawn_seed(self, app_client):
        body = {
            "ticker": "aapl",
            "trigger_type": "price_below",
            "threshold": 150.0,
            "quantity": 2,
        }
        resp = await app_client.post("/api/backtest", json=body)
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert config["ticker"] == "AAPL"
        assert config["side"] == "buy"
        assert config["days"] == 30
        assert config["runs"] == 1
        assert isinstance(config["seed"], int)  # drawn and echoed
        assert config["seed"] >= 0

    async def test_monte_carlo_runs_summary(self, app_client):
        body = {**BODY, "days": 5, "runs": 3}
        resp = await app_client.post("/api/backtest", json=body)
        assert resp.status_code == 200
        assert resp.json()["runs_summary"]["runs"] == 3

    @pytest.mark.parametrize(
        ("overrides", "expected_error"),
        [
            pytest.param({"ticker": "ZZZZ"}, "Ticker not found", id="unknown-ticker"),
            pytest.param(
                {"side": "sell"},
                "Backtest supports buy-entry strategies only", id="sell-side",
            ),
            pytest.param(
                {"trigger_type": "price_crosses"}, "trigger_type must be one of",
                id="bad-trigger",
            ),
            pytest.param(
                {"trigger_type": "price_above", "threshold": 0},
                "Threshold must be greater than 0 for price triggers",
                id="zero-price-threshold",
            ),
            pytest.param(
                {"trigger_type": "price_below", "threshold": -10},
                "Threshold must be greater than 0 for price triggers",
                id="negative-price-threshold",
            ),
            pytest.param({"quantity": 0}, "Quantity must be greater than 0", id="zero-qty"),
            pytest.param({"quantity": -3}, "Quantity must be greater than 0", id="negative-qty"),
            pytest.param({"days": 4}, "days must be between 5 and 120", id="days-low"),
            pytest.param({"days": 121}, "days must be between 5 and 120", id="days-high"),
            pytest.param({"runs": 0}, "runs must be between 1 and 50", id="runs-low"),
            pytest.param({"runs": 51}, "runs must be between 1 and 50", id="runs-high"),
            pytest.param(
                {"take_profit_pct": 0}, "take_profit_pct must be greater than 0", id="zero-tp"
            ),
            pytest.param(
                {"stop_loss_pct": -2}, "stop_loss_pct must be greater than 0", id="negative-sl"
            ),
        ],
    )
    async def test_validation_matrix(self, app_client, overrides, expected_error):
        body = dict(BODY)
        body.update(overrides)
        resp = await app_client.post("/api/backtest", json=body)
        assert resp.status_code == 400
        assert expected_error in resp.json()["error"]
