"""Unit tests for ``app.research.run_research_on_conn`` (D4 §2.2/§2.5).

Covers, against crafted daily bars (zero network, zero randomness — history
mode is a deterministic replay):

- the 2..4 candidate-count batch guard (compact failed shape)
- per-candidate failure isolation: bad DSL / missing exit / unknown template
  fail ONE candidate, never the batch; an unknown ticker (no stored bars)
  fails every candidate and only then the batch status
- persisted draft + linked Run Library row (strategy_id on the run row,
  label prefixed "Research: ", draft owned by user_id)
- ranking determinism incl. tie-breaks (traded desc, score desc, win_rate
  desc, original index asc), zero-trade demotion, and the null
  recommendation when the top candidate never traded
- the robustness score formula — a larger drawdown always LOWERS the score
  (the engine's max_drawdown_pct is a non-negative magnitude)
- days default (120) and the history clamp (20..750) echoed in the outcome
- CN profile parity: cash_pct sizing floors to whole board lots (整手)
- the handler NEVER commits — a rollback leaves no rows behind
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.research import (
    DEFAULT_RESEARCH_DAYS,
    RUN_LABEL_PREFIX,
    run_research_on_conn,
)

D0 = date(2026, 1, 1)


def _dates(n: int) -> list[str]:
    return [date.fromordinal(D0.toordinal() + i).isoformat() for i in range(n)]


def _flat_bars(n: int, px: float = 100.0) -> list[dict]:
    return [
        {"date": d, "open": px, "high": px + 0.5, "low": px - 0.5, "close": px}
        for d in _dates(n)
    ]


def _insert_bars(db_file: str, market: str, ticker: str, bars: list[dict]) -> None:
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
                    "sample",
                    "2026-07-01T00:00:00+00:00",
                )
                for b in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


# 30 bars: flat 100 through day 9, a -5% dip on day 10 (the signal day),
# recovery to ~103 by day 15, then a steady slide to ~85. A day_change_pct
# below -4 entry signals ONLY on day 10 and fills at day 11's open (96):
# a 5% take-profit wins during the recovery; a 3% stop-loss loses on the
# slide. No other day moves more than ~2.1%.
def _signal_bars() -> list[dict]:
    bars = _flat_bars(30)
    bars[10].update({"open": 100.0, "high": 100.0, "low": 94.9, "close": 95.0})
    closes = [96.5, 98.0, 99.5, 101.5, 103.0]
    prev = 95.0
    for offset, close in enumerate(closes):
        day = 11 + offset
        open_ = 96.0 if day == 11 else prev
        bars[day].update(
            {
                "open": open_,
                "high": max(open_, close) + 0.6,
                "low": min(open_, close) - 0.5,
                "close": close,
            }
        )
        prev = close
    for day in range(16, 30):
        close = round(prev * 0.98, 2)
        bars[day].update(
            {
                "open": prev,
                "high": prev + 0.3,
                "low": close - 0.3,
                "close": close,
            }
        )
        prev = close
    return bars


ENTRY_DIP = {"all": [{"field": "day_change_pct", "op": "below", "value": -4}]}
ENTRY_NEVER = {"all": [{"field": "price", "op": "below", "value": 10}]}
SIZING_QTY = {"mode": "fixed_qty", "qty": 10}

WINNER = {
    "name": "Winner",
    "hypothesis": "Dip buys recover",
    "entry": ENTRY_DIP,
    "exits": {"take_profit_pct": 5},
    "sizing": SIZING_QTY,
}
LOSER = {
    "name": "Loser",
    "entry": ENTRY_DIP,
    "exits": {"stop_loss_pct": 3},
    "sizing": SIZING_QTY,
}
NEVER_TRADES = {
    "name": "NeverTrades",
    "entry": ENTRY_NEVER,
    "exits": {"take_profit_pct": 5},
    "sizing": SIZING_QTY,
}


@pytest.fixture
def db_file(tmp_path):
    path = str(tmp_path / "research.db")
    init_db(path)
    return path


@pytest.fixture
def price_cache():
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)
    return cache


def _run(db_file, price_cache, candidates, *, ticker="AAPL", days=None, **kwargs):
    """Open a conn, run the handler, COMMIT (the chat turn's job), close."""
    conn = get_conn(db_file)
    try:
        outcome = run_research_on_conn(
            conn,
            price_cache,
            ticker=ticker,
            days=days,
            candidates=candidates,
            user_id="default",
            **kwargs,
        )
        conn.commit()
        return outcome
    finally:
        conn.close()


def _rows(db_file: str, table: str) -> list:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            f"SELECT * FROM {table} ORDER BY created_at ASC, rowid ASC"  # noqa: S608
        ).fetchall()
    finally:
        conn.close()


class TestBatchGuard:
    @pytest.mark.parametrize("count", [0, 1, 5])
    def test_out_of_range_candidate_count_fails_the_batch(
        self, db_file, price_cache, count
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(db_file, price_cache, [dict(WINNER)] * count)
        assert outcome == {
            "status": "failed",
            "ticker": "AAPL",
            "error": f"research needs 2-4 candidates (got {count})",
        }
        assert _rows(db_file, "strategies") == []
        assert _rows(db_file, "backtest_runs") == []

    @pytest.mark.parametrize("count", [2, 4])
    def test_in_range_candidate_counts_run(self, db_file, price_cache, count):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        candidates = [
            {**WINNER, "name": f"Winner {i}"} for i in range(count)
        ]
        outcome = _run(db_file, price_cache, candidates)
        assert outcome["status"] == "completed"
        assert len(outcome["candidates"]) == count
        assert len(_rows(db_file, "strategies")) == count


class TestPerCandidateIsolation:
    def test_bad_candidates_fail_alone_and_batch_completes(
        self, db_file, price_cache
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        candidates = [
            {"name": "Bad DSL",
             "entry": {"all": [{"field": "nope", "op": "above", "value": 1}]},
             "exits": {"take_profit_pct": 5}, "sizing": SIZING_QTY},
            {"name": "No Exit", "entry": ENTRY_DIP, "exits": {},
             "sizing": SIZING_QTY},
            {"name": "Bad Template", "template": "not_a_template"},
            dict(WINNER),
        ]
        outcome = _run(db_file, price_cache, candidates)
        assert outcome["status"] == "completed"
        statuses = [c["status"] for c in outcome["candidates"]]
        assert statuses == ["failed", "failed", "failed", "completed"]
        assert "entry" in outcome["candidates"][0]["error"]
        assert "exit" in outcome["candidates"][1]["error"].lower()
        assert "template" in outcome["candidates"][2]["error"].lower()
        # Failed candidates carry no rank/ids and persist nothing.
        for failed in outcome["candidates"][:3]:
            assert set(failed) == {"name", "status", "error"}
        assert len(_rows(db_file, "strategies")) == 1
        assert len(_rows(db_file, "backtest_runs")) == 1
        # The only completed candidate is rank 1 and recommended (it traded).
        winner = outcome["candidates"][3]
        assert winner["rank"] == 1
        assert winner["traded"] is True
        assert outcome["recommended_strategy_id"] == winner["strategy_id"]

    def test_unknown_ticker_fails_every_candidate_then_the_batch(
        self, db_file, price_cache
    ):
        # No daily_bars stored for ZZZZ — attach_history_bars fails each
        # candidate with the insufficient-history message; zero completed
        # candidates turn the BATCH status to failed (contract §2.2).
        outcome = _run(
            db_file, price_cache, [dict(WINNER), dict(LOSER)], ticker="ZZZZ"
        )
        assert outcome["status"] == "failed"
        assert outcome["ticker"] == "ZZZZ"
        assert [c["status"] for c in outcome["candidates"]] == ["failed", "failed"]
        for candidate in outcome["candidates"]:
            assert "Insufficient history" in candidate["error"]
        assert outcome["recommended_strategy_id"] is None
        assert _rows(db_file, "strategies") == []
        assert _rows(db_file, "backtest_runs") == []

    def test_insufficient_history_fails_candidates_not_raises(
        self, db_file, price_cache
    ):
        _insert_bars(db_file, "us", "AAPL", _flat_bars(10))  # < 20 bars
        outcome = _run(db_file, price_cache, [dict(WINNER), dict(LOSER)])
        assert outcome["status"] == "failed"
        assert all(
            "Insufficient history" in c["error"] for c in outcome["candidates"]
        )


class TestPersistence:
    def test_draft_and_linked_run_persisted(self, db_file, price_cache):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(db_file, price_cache, [dict(WINNER), dict(LOSER)])
        assert outcome["status"] == "completed"

        strategies = _rows(db_file, "strategies")
        runs = _rows(db_file, "backtest_runs")
        assert len(strategies) == 2
        assert len(runs) == 2
        by_id = {row["id"]: row for row in strategies}
        runs_by_strategy = {row["strategy_id"]: row for row in runs}
        for candidate in outcome["candidates"]:
            row = by_id[candidate["strategy_id"]]
            assert row["status"] == "draft"  # research never deploys
            assert row["user_id"] == "default"
            assert row["name"] == candidate["name"]
            run_row = runs_by_strategy[candidate["strategy_id"]]
            assert run_row["id"] == candidate["run_id"]
            assert run_row["label"] == RUN_LABEL_PREFIX + candidate["name"]
            assert json.loads(run_row["stats"]) == candidate["stats"]

    def test_template_merge_matches_create_action_rule(self, db_file, price_cache):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        explicit_exits = {"take_profit_pct": 9}
        outcome = _run(
            db_file,
            price_cache,
            [
                {"name": "Tpl", "template": "dip_buyer"},
                {"name": "Tpl Override", "template": "dip_buyer",
                 "exits": explicit_exits},
            ],
        )
        assert [c["status"] for c in outcome["candidates"]] == [
            "completed",
            "completed",
        ]
        rows = {row["name"]: row for row in _rows(db_file, "strategies")}
        # Template supplies the config; explicit fields override.
        assert json.loads(rows["Tpl"]["entry"]) == {
            "all": [{"field": "day_change_pct", "op": "below", "value": -3}]
        }
        assert json.loads(rows["Tpl"]["exits"]) == {
            "take_profit_pct": 4,
            "stop_loss_pct": 3,
        }
        assert json.loads(rows["Tpl Override"]["exits"]) == explicit_exits
        assert rows["Tpl Override"]["template"] == "dip_buyer"

    def test_handler_never_commits(self, db_file, price_cache):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        conn = get_conn(db_file)
        try:
            outcome = run_research_on_conn(
                conn,
                price_cache,
                ticker="AAPL",
                days=None,
                candidates=[dict(WINNER), dict(LOSER)],
                user_id="default",
            )
            assert outcome["status"] == "completed"
            conn.rollback()  # the chat turn owns the commit — undo it
        finally:
            conn.close()
        assert _rows(db_file, "strategies") == []
        assert _rows(db_file, "backtest_runs") == []


class TestRankingAndScore:
    def test_winner_loser_never_trades_ordering(self, db_file, price_cache):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(
            db_file,
            price_cache,
            [dict(NEVER_TRADES), dict(LOSER), dict(WINNER)],
        )
        assert outcome["status"] == "completed"
        by_name = {c["name"]: c for c in outcome["candidates"]}
        winner, loser, never = (
            by_name["Winner"],
            by_name["Loser"],
            by_name["NeverTrades"],
        )
        assert winner["traded"] is True and winner["stats"]["round_trips"] == 1
        assert loser["traded"] is True and loser["stats"]["win_rate"] == 0.0
        assert never["traded"] is False and never["score"] == 0.0
        assert winner["score"] > 0 > loser["score"]
        # Zero-trade demotion: the losing TRADED candidate outranks the
        # untraded one despite its lower score (0.0 > negative).
        assert winner["rank"] == 1
        assert loser["rank"] == 2
        assert never["rank"] == 3
        assert outcome["recommended_strategy_id"] == winner["strategy_id"]

    def test_score_formula_larger_drawdown_lowers_score(
        self, db_file, price_cache
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(db_file, price_cache, [dict(WINNER), dict(LOSER)])
        for candidate in outcome["candidates"]:
            stats = candidate["stats"]
            assert stats["max_drawdown_pct"] >= 0  # non-negative magnitude
            assert candidate["score"] == round(
                stats["total_return_pct"] - 0.5 * stats["max_drawdown_pct"], 2
            )
            # Sanity: any drawdown strictly lowers the score below raw return.
            if stats["max_drawdown_pct"] > 0:
                assert candidate["score"] < stats["total_return_pct"]

    def test_no_recommendation_when_top_candidate_never_traded(
        self, db_file, price_cache
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(
            db_file,
            price_cache,
            [dict(NEVER_TRADES), {**NEVER_TRADES, "name": "AlsoNever"}],
        )
        assert outcome["status"] == "completed"
        assert [c["rank"] for c in outcome["candidates"]] == [1, 2]
        assert all(c["traded"] is False for c in outcome["candidates"])
        # An untraded winner is not a recommendation (contract §2.2).
        assert outcome["recommended_strategy_id"] is None


class TestRankingTieBreaks:
    """Exact tie-break determinism via canned engine stats.

    ``app.research.run_backtest`` is monkeypatched to return one canned
    stats dict per candidate (in candidate order) so the sort inputs are
    exact; everything else (merge, validation, bars, persistence) is real.
    """

    @staticmethod
    def _fake_result(stats: dict) -> dict:
        return {
            "config": {"ticker": "AAPL", "days": 120},
            "stats": stats,
            "equity_curve": [],
            "baseline_curve": [],
            "trades": [],
            "runs_summary": None,
        }

    def _run_with_stats(self, db_file, price_cache, monkeypatch, stats_list):
        canned = list(stats_list)

        def fake_run_backtest(config, **kwargs):
            return self._fake_result(canned.pop(0))

        monkeypatch.setattr("app.research.run_backtest", fake_run_backtest)
        candidates = [
            {**WINNER, "name": f"C{i}"} for i in range(len(stats_list))
        ]
        return _run(db_file, price_cache, candidates)

    @staticmethod
    def _stats(return_pct, drawdown_pct, round_trips, win_rate):
        return {
            "total_return_pct": return_pct,
            "max_drawdown_pct": drawdown_pct,
            "final_equity": 10_000.0,
            "fires": round_trips,
            "round_trips": round_trips,
            "win_rate": win_rate,
            "commission_paid": 0.0,
            "rejections": {"insufficient_cash": 0},
        }

    def test_score_breaks_before_win_rate(self, db_file, price_cache, monkeypatch):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = self._run_with_stats(
            db_file,
            price_cache,
            monkeypatch,
            [
                self._stats(10.0, 4.0, 3, 0.9),  # score 8.0
                self._stats(12.0, 2.0, 3, 0.3),  # score 11.0 — wins on score
            ],
        )
        assert [c["rank"] for c in outcome["candidates"]] == [2, 1]

    def test_win_rate_breaks_score_ties(self, db_file, price_cache, monkeypatch):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = self._run_with_stats(
            db_file,
            price_cache,
            monkeypatch,
            [
                self._stats(10.0, 2.0, 3, 0.4),  # score 9.0
                self._stats(11.0, 4.0, 3, 0.8),  # score 9.0, higher win rate
            ],
        )
        assert [c["rank"] for c in outcome["candidates"]] == [2, 1]

    def test_original_index_breaks_full_ties(
        self, db_file, price_cache, monkeypatch
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        same = self._stats(10.0, 2.0, 3, 0.5)
        outcome = self._run_with_stats(
            db_file, price_cache, monkeypatch, [dict(same), dict(same)]
        )
        assert [c["rank"] for c in outcome["candidates"]] == [1, 2]
        assert outcome["recommended_strategy_id"] == (
            outcome["candidates"][0]["strategy_id"]
        )

    def test_traded_outranks_higher_untraded_score(
        self, db_file, price_cache, monkeypatch
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = self._run_with_stats(
            db_file,
            price_cache,
            monkeypatch,
            [
                self._stats(0.0, 0.0, 0, None),  # untraded, score 0.0
                self._stats(-2.0, 1.0, 2, 0.5),  # traded, score -2.5
            ],
        )
        assert [c["rank"] for c in outcome["candidates"]] == [2, 1]
        # The traded rank-1 candidate IS the recommendation.
        assert outcome["recommended_strategy_id"] == (
            outcome["candidates"][1]["strategy_id"]
        )


class TestDaysWindow:
    def test_days_defaults_to_120(self, db_file, price_cache):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(db_file, price_cache, [dict(WINNER), dict(LOSER)])
        assert outcome["days"] == DEFAULT_RESEARCH_DAYS == 120

    @pytest.mark.parametrize(("requested", "effective"), [(5, 20), (10_000, 750)])
    def test_days_clamped_to_history_bounds(
        self, db_file, price_cache, requested, effective
    ):
        _insert_bars(db_file, "us", "AAPL", _signal_bars())
        outcome = _run(
            db_file, price_cache, [dict(WINNER), dict(LOSER)], days=requested
        )
        assert outcome["days"] == effective
        assert outcome["status"] == "completed"


class TestCNProfileParity:
    def test_cash_pct_floors_to_whole_lots_on_cn(self, tmp_path):
        # 600519 at ~100 with the CN seed cash (¥100k): a 20% cash_pct
        # budget (~¥20k) buys 2 whole lots (200 shares), never odd lots.
        db_file = str(tmp_path / "research_cn.db")
        init_db(
            db_file,
            seed_cash=CN_PROFILE.seed_cash,
            default_watchlist=list(CN_PROFILE.universe.default_watchlist),
        )
        _insert_bars(db_file, "cn", "600519", _signal_bars())
        cache = PriceCache()
        for ticker, price in CN_PROFILE.universe.seed_prices.items():
            cache.update(ticker, price)

        outcome = _run(
            db_file,
            cache,
            [
                {"name": "CN Dip", "entry": ENTRY_DIP,
                 "exits": {"take_profit_pct": 5},
                 "sizing": {"mode": "cash_pct", "pct": 20}},
                {"name": "CN Dip Stop", "entry": ENTRY_DIP,
                 "exits": {"stop_loss_pct": 3},
                 "sizing": {"mode": "cash_pct", "pct": 20}},
            ],
            ticker="600519",
            universe=CN_PROFILE.universe,
            profile=CN_PROFILE,
            market="cn",
            starting_cash=CN_PROFILE.seed_cash,
        )
        assert outcome["status"] == "completed"
        assert all(c["status"] == "completed" for c in outcome["candidates"])
        assert all(c["traded"] for c in outcome["candidates"])
        # The persisted run's fills are whole board lots (T+1 and fees ride
        # the same engine path as every other CN strategy backtest).
        conn = get_conn(db_file)
        try:
            runs = conn.execute("SELECT trades FROM backtest_runs").fetchall()
        finally:
            conn.close()
        assert len(runs) == 2
        for row in runs:
            trades = json.loads(row["trades"])
            buys = [t for t in trades if t["side"] == "buy"]
            assert buys
            assert all(t["quantity"] % 100 == 0 for t in buys)
