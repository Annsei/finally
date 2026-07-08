"""Golden-sample regression tests for the backtest engine (P2 contract §0/§4).

The fixtures in tests/golden/ were captured from the PRE-P2 engine (before the
condition-group/exits unification) with pinned (config, seed, end_time). The
legacy ``POST /api/backtest`` semantics — bar generation, RNG draw order,
fees/half-spread, daily re-arm, SL-before-TP, T+1 deferral, downsampling, and
the legacy config echo shape — must stay byte-for-byte identical for old
requests forever. These tests re-run the engine and compare the COMPLETE
response payload against the stored fixture via canonical JSON.

Do not regenerate the fixtures to make a failing test pass: a diff here means
the engine's legacy behavior changed, which is a contract violation.

Fixture inventory (all end_time=1750000000.0, anchors from seed prices):
- us_basic_seed1234:   AAPL day_change_pct_below -1, qty 10, no exits, 1 run,
                       commission 0, US $10k cash, no profile.
- us_tpsl_mc_seed5678: TSLA day_change_pct_below -2, qty 5, tp 3 / sl 2,
                       runs=10 Monte Carlo, commission 0, US $10k, no profile.
- cn_lot_seed9012:     600036 day_change_pct_below -1, whole-lot qty 100,
                       tp 4 / sl 3, CN_PROFILE (fees floor + stamp + T+1),
                       commission 2.5 bps, ¥100k starting cash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest import normalize_backtest_config, run_backtest
from app.market.cache import PriceCache
from app.market.profiles import CN_PROFILE

GOLDEN_DIR = Path(__file__).parent / "golden"
END_TIME = 1_750_000_000.0


def _canonical(payload: dict) -> str:
    """Canonical JSON encoding — key-sorted, minimal separators.

    Python floats round-trip exactly through json (shortest-repr), so equal
    canonical strings <=> byte-identical payloads.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / f"backtest_{name}.json").read_text(encoding="utf-8"))


def _run_us_basic() -> dict:
    outcome = normalize_backtest_config(
        PriceCache(),
        ticker="AAPL",
        trigger_type="day_change_pct_below",
        threshold=-1.0,
        quantity=10,
        seed=1234,
    )
    assert outcome["status"] == "ok"
    return run_backtest(outcome["config"], commission_bps=0.0, end_time=END_TIME)


def _run_us_tpsl_mc() -> dict:
    outcome = normalize_backtest_config(
        PriceCache(),
        ticker="TSLA",
        trigger_type="day_change_pct_below",
        threshold=-2.0,
        quantity=5,
        take_profit_pct=3.0,
        stop_loss_pct=2.0,
        runs=10,
        seed=5678,
    )
    assert outcome["status"] == "ok"
    return run_backtest(outcome["config"], commission_bps=0.0, end_time=END_TIME)


def _run_cn_lot() -> dict:
    outcome = normalize_backtest_config(
        PriceCache(),
        ticker="600036",
        trigger_type="day_change_pct_below",
        threshold=-1.0,
        quantity=100,
        take_profit_pct=4.0,
        stop_loss_pct=3.0,
        seed=9012,
        universe=CN_PROFILE.universe,
        profile=CN_PROFILE,
    )
    assert outcome["status"] == "ok"
    return run_backtest(
        outcome["config"],
        commission_bps=CN_PROFILE.default_commission_bps,
        end_time=END_TIME,
        starting_cash=CN_PROFILE.seed_cash,
        profile=CN_PROFILE,
    )


_CASES = {
    "us_basic_seed1234": _run_us_basic,
    "us_tpsl_mc_seed5678": _run_us_tpsl_mc,
    "cn_lot_seed9012": _run_cn_lot,
}


class TestBacktestGolden:
    """Full-payload byte-exactness against the pre-P2 golden fixtures."""

    @pytest.mark.parametrize("name", sorted(_CASES))
    def test_payload_matches_golden_fixture(self, name):
        actual = _CASES[name]()
        expected = _load_golden(name)
        # Compare canonical strings — any drift (values, key set, rounding,
        # ordering inside lists) fails. Structured diff first for readability.
        assert actual == expected
        assert _canonical(actual) == _canonical(expected)

    @pytest.mark.parametrize("name", sorted(_CASES))
    def test_legacy_config_echo_shape_frozen(self, name):
        """Old requests keep the OLD config echo — no entry/exits/sizing keys."""
        actual = _CASES[name]()
        assert set(actual["config"].keys()) == {
            "ticker",
            "trigger_type",
            "threshold",
            "side",
            "quantity",
            "take_profit_pct",
            "stop_loss_pct",
            "days",
            "runs",
            "seed",
            "commission_bps",
            "anchor_price",
        }

    def test_fixtures_exercise_real_paths(self):
        """Guard against silently-degenerate fixtures (no fires = no coverage)."""
        us_basic = _load_golden("us_basic_seed1234")
        us_mc = _load_golden("us_tpsl_mc_seed5678")
        cn = _load_golden("cn_lot_seed9012")
        assert us_basic["stats"]["fires"] >= 1
        assert us_mc["stats"]["fires"] >= 1
        assert us_mc["runs_summary"] is not None
        assert us_mc["runs_summary"]["runs"] == 10
        assert any(t["reason"] in {"take_profit", "stop_loss"} for t in us_mc["trades"])
        assert cn["stats"]["fires"] >= 1
        assert cn["stats"]["commission_paid"] > 0  # CN fee floor really applied
