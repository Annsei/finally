"""History backtest mode tests (D1 contract §3/§6) — crafted daily bars.

Engine-level tests drive ``normalize_* -> attach_history_bars ->
run_backtest`` directly on crafted OHLC fixtures inserted into daily_bars;
route-level tests pin the HTTP contract (POST /api/backtest ``source`` field,
Run Library passthrough). ZERO network and ZERO randomness — history mode is
a deterministic replay.

Covers: T+1 open fills (signal at close T -> entry at T+1's open, spread
applied), the SL -> trailing -> TP priority against the day's low/high,
entry-day exits on us vs the CN T+1 skip, trailing high-water ratchet,
max_holding_days, CN 整手 sizing + fee floor + stamp tax reuse, insufficient
bars -> 400, runs > 1 -> 400, days clamping (20..750), the config echo
additions (source/date_range/entry_fill, seed null) with the synthetic echo
untouched, deterministic re-runs, and Run Library / strategy passthrough.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.backtest import (
    attach_history_bars,
    normalize_backtest_config,
    normalize_strategy_backtest_config,
    run_backtest,
)
from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.simulator import spread_bps_for
from app.routes.backtest import create_backtest_router
from app.routes.backtest_runs import create_backtest_runs_router

D0 = date(2026, 1, 1)


def _dates(n: int) -> list[str]:
    return [date.fromordinal(D0.toordinal() + i).isoformat() for i in range(n)]


def _unix(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _flat_bars(n: int, px: float = 100.0) -> list[dict]:
    return [
        {"date": d, "open": px, "high": px + 0.5, "low": px - 0.5, "close": px}
        for d in _dates(n)
    ]


def _insert_bars(db_file: str, market: str, ticker: str, bars: list[dict], source="sample"):
    conn = get_conn(db_file)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars (market, ticker, date, open, high, "
            "low, close, volume, source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    market,
                    ticker,
                    b["date"],
                    b["open"],
                    b["high"],
                    b["low"],
                    b["close"],
                    b.get("volume", 1000.0),
                    source,
                    "2026-07-01T00:00:00+00:00",
                )
                for b in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _run_history(
    db_file: str,
    *,
    market: str = "us",
    profile=None,
    starting_cash: float = 10_000.0,
    commission_bps: float = 0.0,
    strategy: dict | None = None,
    **fields,
) -> dict:
    """normalize -> attach -> run, asserting the normalize/attach succeeded."""
    universe = profile.universe if profile is not None else None
    if strategy is not None:
        outcome = normalize_strategy_backtest_config(
            PriceCache(), universe=universe, profile=profile, source="history", **strategy
        )
    else:
        outcome = normalize_backtest_config(
            PriceCache(), universe=universe, profile=profile, source="history", **fields
        )
    assert outcome["status"] == "ok", outcome
    conn = get_conn(db_file)
    try:
        error = attach_history_bars(outcome["config"], conn, market=market)
    finally:
        conn.close()
    assert error is None, error
    return run_backtest(
        outcome["config"],
        commission_bps=commission_bps,
        end_time=1_750_000_000.0,
        starting_cash=starting_cash,
        profile=profile,
    )


@pytest.fixture
def db_file(tmp_path):
    path = str(tmp_path / "hist.db")
    init_db(path)
    return path


# 24 bars: flat 100 through index 19, -5% close on day 20 (the signal),
# recovery afterwards. Signal day 20 -> fill at day 21's open (96).
def _signal_bars() -> list[dict]:
    bars = _flat_bars(24)
    bars[20].update({"open": 100.0, "high": 100.0, "low": 94.9, "close": 95.0})
    bars[21].update({"open": 96.0, "high": 97.0, "low": 95.5, "close": 96.5})
    bars[22].update({"open": 96.5, "high": 98.0, "low": 96.0, "close": 97.0})
    bars[23].update({"open": 97.0, "high": 98.5, "low": 96.5, "close": 98.0})
    return bars


LEGACY = {
    "ticker": "AAPL",
    "trigger_type": "day_change_pct_below",
    "threshold": -3.0,
    "quantity": 10,
}


class TestEntryFillTPlusOne:
    def test_signal_at_close_fills_next_open(self, db_file):
        bars = _signal_bars()
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(db_file, **LEGACY)

        hs = spread_bps_for("AAPL") / 2.0 / 10_000.0
        buy, sell = result["trades"]
        # Fill day = signal day + 1, at the OPEN (96), ask side of the spread.
        assert buy["side"] == "buy" and buy["reason"] == "trigger"
        assert buy["time"] == _unix(bars[21]["date"])
        assert buy["price"] == round(96.0 * (1.0 + hs), 2)
        assert buy["quantity"] == 10.0
        # No exits configured -> horizon end at the final close.
        assert sell["reason"] == "horizon_end"
        assert sell["time"] == _unix(bars[23]["date"])
        assert sell["price"] == round(98.0 * (1.0 - hs), 2)
        assert result["stats"]["fires"] == 1
        assert result["stats"]["round_trips"] == 1
        assert result["runs_summary"] is None

    def test_final_bar_signal_never_fills(self, db_file):
        bars = _flat_bars(24)
        # The ONLY signal lands on the last bar — no T+1 day exists.
        bars[23].update({"open": 100.0, "high": 100.0, "low": 94.9, "close": 95.0})
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(db_file, **LEGACY)
        assert result["trades"] == []
        assert result["stats"]["fires"] == 0

    def test_baseline_is_buy_hold_from_first_open(self, db_file):
        bars = _signal_bars()
        bars[0]["open"] = 98.0  # first open != first close
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(db_file, **LEGACY)
        first = result["baseline_curve"][0]
        last = result["baseline_curve"][-1]
        assert first["value"] == round(10_000.0 * 100.0 / 98.0, 2)
        assert last["value"] == round(10_000.0 * 98.0 / 98.0, 2)
        assert result["stats"]["buy_hold_return_pct"] == 0.0  # 98 -> 98

    def test_equity_curve_marks_daily_closes(self, db_file):
        bars = _signal_bars()
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(db_file, **LEGACY)
        assert len(result["equity_curve"]) == 24  # one point per trading day
        assert [p["time"] for p in result["equity_curve"]] == [
            _unix(b["date"]) for b in bars
        ]
        # Before the entry, equity is flat cash.
        assert result["equity_curve"][10]["value"] == 10_000.0


class TestExitPriority:
    def _entry_day_bars(self) -> list[dict]:
        """Signal on day 20; day 21 touches BOTH the stop and the target.

        Days 22/23 hold within ±3% of the prior close so the trigger cannot
        re-fire after the stop-out — exactly one round trip.
        """
        bars = _signal_bars()
        bars[21].update({"open": 96.0, "high": 104.0, "low": 92.0, "close": 100.0})
        bars[22].update({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5})
        bars[23].update({"open": 100.5, "high": 101.0, "low": 99.5, "close": 100.0})
        return bars

    def test_same_day_double_touch_is_a_stop(self, db_file):
        _insert_bars(db_file, "us", "AAPL", self._entry_day_bars())
        result = _run_history(
            db_file, **LEGACY, take_profit_pct=5.0, stop_loss_pct=2.0
        )
        hs = spread_bps_for("AAPL") / 2.0 / 10_000.0
        buy, sell = result["trades"]
        sl_level = 96.0 * (1.0 + hs) * 0.98
        # us profile: the entry day itself may exit (entry was at the open),
        # and a bar touching both SL and TP counts as a stop (priority).
        assert sell["reason"] == "stop_loss"
        assert sell["time"] == buy["time"] == _unix(self._entry_day_bars()[21]["date"])
        assert sell["price"] == round(sl_level * (1.0 - hs), 2)

    def test_take_profit_fills_at_the_trigger_level(self, db_file):
        bars = _signal_bars()
        bars[22].update({"open": 96.5, "high": 102.0, "low": 96.2, "close": 101.0})
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(db_file, **LEGACY, take_profit_pct=3.0)
        hs = spread_bps_for("AAPL") / 2.0 / 10_000.0
        sell = result["trades"][1]
        tp_level = 96.0 * (1.0 + hs) * 1.03
        assert sell["reason"] == "take_profit"
        assert sell["time"] == _unix(bars[22]["date"])
        assert sell["price"] == round(tp_level * (1.0 - hs), 2)

    def test_trailing_stop_uses_prior_high_water(self, db_file):
        bars = _flat_bars(24)
        bars[20].update({"open": 100.0, "high": 100.0, "low": 94.9, "close": 95.0})
        bars[21].update({"open": 100.0, "high": 100.0, "low": 99.0, "close": 100.0})
        bars[22].update({"open": 110.0, "high": 120.0, "low": 109.0, "close": 118.0})
        bars[23].update({"open": 112.0, "high": 113.0, "low": 105.0, "close": 106.0})
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(
            db_file,
            strategy={
                "ticker": "AAPL",
                "entry": {"all": [{"field": "day_change_pct", "op": "below", "value": -3.0}]},
                "exits": {"trailing_stop_pct": 10.0},
                "sizing": {"mode": "fixed_qty", "qty": 10},
            },
        )
        hs = spread_bps_for("AAPL") / 2.0 / 10_000.0
        buy, sell = result["trades"]
        assert buy["time"] == _unix(bars[21]["date"])
        # Day 22's 120 high ratchets the water mark AFTER that day's checks;
        # day 23's 105 low pierces 120 * 0.9 = 108 -> trail exit at 108.
        assert sell["reason"] == "trailing_stop"
        assert sell["time"] == _unix(bars[23]["date"])
        assert sell["price"] == round(120.0 * 0.9 * (1.0 - hs), 2)

    def test_max_holding_days_closes_at_the_close(self, db_file):
        bars = _signal_bars()
        _insert_bars(db_file, "us", "AAPL", bars)
        result = _run_history(
            db_file,
            strategy={
                "ticker": "AAPL",
                "entry": {"all": [{"field": "day_change_pct", "op": "below", "value": -3.0}]},
                "exits": {"max_holding_days": 2},
                "sizing": {"mode": "fixed_qty", "qty": 10},
            },
        )
        hs = spread_bps_for("AAPL") / 2.0 / 10_000.0
        buy, sell = result["trades"]
        assert buy["time"] == _unix(bars[21]["date"])  # entry day (index 21)
        assert sell["reason"] == "max_holding_days"
        assert sell["time"] == _unix(bars[23]["date"])  # 23 - 21 >= 2
        assert sell["price"] == round(98.0 * (1.0 - hs), 2)  # at the close


class TestCnMechanics:
    def _cn_bars(self) -> list[dict]:
        bars = _flat_bars(24, 35.0)
        bars[20].update({"open": 35.0, "high": 35.0, "low": 33.2, "close": 33.25})
        # Entry day (21): the low pierces any near stop — T+1 must skip it.
        bars[21].update({"open": 33.5, "high": 33.8, "low": 32.0, "close": 33.6})
        bars[22].update({"open": 33.6, "high": 34.0, "low": 32.0, "close": 33.8})
        bars[23].update({"open": 33.8, "high": 34.2, "low": 33.5, "close": 34.0})
        return bars

    def test_t1_skips_entry_day_exit(self, db_file):
        _insert_bars(db_file, "cn", "600036", self._cn_bars())
        result = _run_history(
            db_file,
            market="cn",
            profile=CN_PROFILE,
            starting_cash=CN_PROFILE.seed_cash,
            commission_bps=CN_PROFILE.default_commission_bps,
            ticker="600036",
            trigger_type="day_change_pct_below",
            threshold=-3.0,
            quantity=100,
            stop_loss_pct=3.0,
        )
        buy, sell = result["trades"][0], result["trades"][1]
        bars = self._cn_bars()
        # Entry day 21's low (32.0) is far below the stop, but under T+1 the
        # position cannot exit until day 22 (入场次日起可出).
        assert buy["time"] == _unix(bars[21]["date"])
        assert sell["reason"] == "stop_loss"
        assert sell["time"] == _unix(bars[22]["date"])

    def test_lot_validation_and_cash_pct_flooring(self, db_file):
        _insert_bars(db_file, "cn", "600036", self._cn_bars())
        # 整手: a non-lot fixed quantity fails validation (same zh message).
        outcome = normalize_backtest_config(
            PriceCache(),
            ticker="600036",
            trigger_type="day_change_pct_below",
            threshold=-3.0,
            quantity=150,
            universe=CN_PROFILE.universe,
            profile=CN_PROFILE,
            source="history",
        )
        assert outcome["status"] == "failed"
        assert "100" in outcome["error"]

        # cash_pct sizing floors to whole board lots: 50% of ¥100k at ~33.5
        # buys 1492 shares -> floored to 1400 (14 lots).
        result = _run_history(
            db_file,
            market="cn",
            profile=CN_PROFILE,
            starting_cash=CN_PROFILE.seed_cash,
            commission_bps=CN_PROFILE.default_commission_bps,
            strategy={
                "ticker": "600036",
                "entry": {"all": [{"field": "day_change_pct", "op": "below", "value": -3.0}]},
                "exits": {},
                "sizing": {"mode": "cash_pct", "pct": 50},
            },
        )
        buy = result["trades"][0]
        assert buy["quantity"] % 100 == 0
        assert buy["quantity"] == 1400.0

    def test_fee_floor_and_stamp_tax_reuse(self, db_file):
        """Fees reuse the engine formula: floor ¥5 per leg + sell stamp."""
        _insert_bars(db_file, "cn", "600036", self._cn_bars())
        result = _run_history(
            db_file,
            market="cn",
            profile=CN_PROFILE,
            starting_cash=CN_PROFILE.seed_cash,
            commission_bps=CN_PROFILE.default_commission_bps,
            ticker="600036",
            trigger_type="day_change_pct_below",
            threshold=-3.0,
            quantity=100,
        )
        hs = spread_bps_for("600036") / 2.0 / 10_000.0
        bars = self._cn_bars()
        buy_px = bars[21]["open"] * (1.0 + hs)
        sell_px = bars[23]["close"] * (1.0 - hs)
        buy_notional = 100 * buy_px
        sell_notional = 100 * sell_px
        # Commission = max(¥5, notional * 2.5bps) = ¥5 on both ~¥3.4k legs;
        # the sell leg adds 5bps stamp tax.
        expected = 5.0 + 5.0 + sell_notional * 5.0 / 10_000.0
        assert buy_notional * 2.5 / 10_000.0 < 5.0  # the floor really binds
        assert result["stats"]["commission_paid"] == round(expected, 2)


class TestValidationAndEcho:
    def test_insufficient_bars_message(self, db_file):
        _insert_bars(db_file, "us", "AAPL", _flat_bars(10))
        outcome = normalize_backtest_config(PriceCache(), source="history", **LEGACY)
        assert outcome["status"] == "ok"
        conn = get_conn(db_file)
        try:
            error = attach_history_bars(outcome["config"], conn, market="us")
        finally:
            conn.close()
        assert error is not None
        assert error.startswith("Insufficient history — run a data sync first")
        assert "10 daily bars stored for AAPL" in error  # coverage hint

    def test_runs_gt_one_fails(self):
        outcome = normalize_backtest_config(
            PriceCache(), source="history", runs=5, **LEGACY
        )
        assert outcome["status"] == "failed"
        assert "runs must be 1 for history backtests" in outcome["error"]

    def test_bad_source_fails(self):
        outcome = normalize_backtest_config(PriceCache(), source="magic", **LEGACY)
        assert outcome["status"] == "failed"
        assert "source must be 'synthetic' or 'history'" in outcome["error"]

    def test_days_clamped_not_rejected(self, db_file):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        # days=5 clamps up to 20 (the last 20 of 24 stored bars)...
        result = _run_history(db_file, days=5, **LEGACY)
        assert result["config"]["days"] == 20
        assert result["config"]["date_range"]["from"] == _dates(24)[4]
        # ...and days=9999 clamps down to 750 (all 24 available bars used).
        result = _run_history(db_file, days=9999, **LEGACY)
        assert result["config"]["days"] == 750
        assert result["config"]["date_range"] == {
            "from": _dates(24)[0],
            "to": _dates(24)[23],
        }

    def test_seed_ignored_and_echoed_null(self, db_file):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        result = _run_history(db_file, seed=12345, **LEGACY)
        assert result["config"]["seed"] is None

    def test_history_echo_additions_and_anchor(self, db_file):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        result = _run_history(db_file, **LEGACY)
        config = result["config"]
        assert config["source"] == "sample"  # the evaluated bars' source
        assert config["date_range"] == {"from": _dates(24)[0], "to": _dates(24)[23]}
        assert config["entry_fill"] == "next_open"  # the no-look-ahead marker
        assert config["anchor_price"] == 98.0  # last stored close, not live
        assert result["runs_summary"] is None

    def test_synthetic_echo_gains_no_new_keys(self):
        """The default path stays byte-shape identical (golden invariant)."""
        outcome = normalize_backtest_config(PriceCache(), seed=1, **LEGACY)
        assert outcome["status"] == "ok"
        result = run_backtest(outcome["config"], end_time=1_750_000_000.0)
        assert set(result["config"]) == {
            "ticker", "trigger_type", "threshold", "side", "quantity",
            "take_profit_pct", "stop_loss_pct", "days", "runs", "seed",
            "commission_bps", "anchor_price",
        }

    def test_history_replay_is_deterministic(self, db_file):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        a = _run_history(db_file, **LEGACY, take_profit_pct=4.0, stop_loss_pct=2.0)
        b = _run_history(db_file, **LEGACY, take_profit_pct=4.0, stop_loss_pct=2.0)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_unknown_ticker_defers_to_bar_lookup(self, db_file):
        """A synced ticker outside the live universe still backtests."""
        _insert_bars(db_file, "us", "ZZZT", _signal_bars())
        result = _run_history(
            db_file,
            ticker="ZZZT",
            trigger_type="day_change_pct_below",
            threshold=-3.0,
            quantity=1,
        )
        assert result["stats"]["fires"] == 1
        # And with NO bars at all, the insufficient-history error names it.
        outcome = normalize_backtest_config(
            PriceCache(),
            ticker="NOWHERE",
            trigger_type="price_above",
            threshold=1.0,
            quantity=1,
            source="history",
        )
        assert outcome["status"] == "ok"
        conn = get_conn(db_file)
        try:
            error = attach_history_bars(outcome["config"], conn, market="us")
        finally:
            conn.close()
        assert "no daily bars stored for NOWHERE" in error


@pytest_asyncio.fixture
async def route_api(tmp_path):
    """Backtest + Run Library routers wired like main.py, with seeded bars."""
    db_file = str(tmp_path / "route.db")
    init_db(db_file)
    _insert_bars(db_file, "us", "AAPL", _signal_bars())
    price_cache = PriceCache()
    price_cache.update("AAPL", 190.0)
    app = FastAPI()
    app.include_router(create_backtest_router(price_cache, db_path=db_file))
    app.include_router(create_backtest_runs_router(price_cache, db_file))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield type("Ctx", (), {"client": client, "db": db_file})()


@pytest.mark.asyncio
class TestRoutes:
    async def test_post_backtest_history_source(self, route_api):
        resp = await route_api.client.post(
            "/api/backtest", json={**LEGACY, "source": "history"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["source"] == "sample"
        assert data["config"]["seed"] is None
        assert data["config"]["date_range"]["to"] == _dates(24)[23]
        assert data["stats"]["fires"] == 1
        assert data["runs_summary"] is None

    async def test_post_backtest_history_runs_gt_one_400(self, route_api):
        resp = await route_api.client.post(
            "/api/backtest", json={**LEGACY, "source": "history", "runs": 3}
        )
        assert resp.status_code == 400
        assert "runs must be 1" in resp.json()["error"]

    async def test_post_backtest_insufficient_history_400(self, route_api):
        resp = await route_api.client.post(
            "/api/backtest",
            json={
                "ticker": "MSFT",  # no bars synced for MSFT
                "trigger_type": "price_above",
                "threshold": 1.0,
                "quantity": 1,
                "source": "history",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"].startswith("Insufficient history")

    async def test_post_backtest_bad_source_400(self, route_api):
        resp = await route_api.client.post(
            "/api/backtest", json={**LEGACY, "source": "hogwash"}
        )
        assert resp.status_code == 400

    async def test_synthetic_requests_unchanged(self, route_api):
        resp = await route_api.client.post("/api/backtest", json={**LEGACY, "seed": 7})
        assert resp.status_code == 200
        assert "source" not in resp.json()["config"]
        assert "date_range" not in resp.json()["config"]

    async def test_run_library_persists_history_source(self, route_api):
        resp = await route_api.client.post(
            "/api/backtest/runs", json={**LEGACY, "source": "history", "label": "h1"}
        )
        assert resp.status_code == 201
        run = resp.json()["run"]
        assert run["config"]["source"] == "sample"
        assert run["config"]["date_range"]["from"] == _dates(24)[0]
        assert run["runs_summary"] is None

        # The list row surfaces the data source for the badge (§5)...
        listing = await route_api.client.get("/api/backtest/runs")
        item = listing.json()["runs"][0]
        assert item["source"] == "sample"
        assert item["seed"] is None

        # ...and synthetic list rows stay shape-identical (no source key).
        await route_api.client.post("/api/backtest/runs", json={**LEGACY, "seed": 3})
        listing = await route_api.client.get("/api/backtest/runs")
        synthetic = listing.json()["runs"][0]
        assert "source" not in synthetic

    async def test_strategy_run_passes_source_through(self, route_api):
        """POST /runs {strategy_id, source: history} — the §5 detail flow."""
        conn = get_conn(route_api.db)
        try:
            conn.execute(
                "INSERT INTO strategies (id, user_id, name, ticker, status, entry, "
                "exits, sizing, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "strat-1",
                    "default",
                    "Dip buyer",
                    "AAPL",
                    "draft",
                    json.dumps({"all": [{"field": "day_change_pct", "op": "below", "value": -3.0}]}),
                    json.dumps({"stop_loss_pct": 5}),
                    json.dumps({"mode": "fixed_qty", "qty": 2}),
                    "2026-07-01T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        resp = await route_api.client.post(
            "/api/backtest/runs", json={"strategy_id": "strat-1", "source": "history"}
        )
        assert resp.status_code == 201
        run = resp.json()["run"]
        assert run["strategy_id"] == "strat-1"
        # Strategy-shaped config keeps entry/exits/sizing; source echoes the
        # DATA source (the "strategy" marker is overridden for the badge).
        assert run["config"]["entry"]["all"][0]["field"] == "day_change_pct"
        assert run["config"]["source"] == "sample"
        assert run["config"]["seed"] is None
        listing = await route_api.client.get("/api/backtest/runs?strategy_id=strat-1")
        assert listing.json()["runs"][0]["source"] == "sample"
