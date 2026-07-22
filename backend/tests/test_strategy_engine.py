"""Live strategy engine single-step tests (P2 §3/§10).

Drives ``process_strategies_once`` against a seeded temp DB + PriceCache:

- entry fires → trade (strategy_id attributed) + kind='strategy' chat row +
  portfolio snapshot land together and the row's open state is written
- exit priority stop_loss → trailing_stop → take_profit → max_holding_days
  (pure ``exit_reason`` matrix + a full engine pass)
- trailing high-water mark rises with the live price and is written back
- max_holding_days exits on UTC calendar-day age
- T+1 (CN profile): no exit on the entry's calendar day — but the high-water
  mark still tracks
- cash_pct sizing floors to whole shares and whole CN board lots; a budget
  under one lot skips and stamps the 60s cooldown
- a sell rejected for insufficient shares (user sold out manually) clears
  the open state and leaves a chat note instead of dead-looping
- a transiently rejected exit (market closed) skips with NO state change
- paused/archived strategies are never evaluated
- one bad strategy rolls back and never stops the pass (isolation)
- an active cooldown skips evaluation; an expired one recovers
- a failed entry trade (insufficient cash) stamps the cooldown

The rejection/isolation scenarios monkeypatch
``strategy_engine.execute_trade_on_conn`` — the module aliases
``_execute_trade_impl`` under that name expressly for this.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app import strategy_engine
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.strategy_engine import (
    COOLDOWN_SECONDS,
    entry_quantity,
    exit_reason,
    process_strategies_once,
)

ALWAYS_ENTRY = {"all": [{"field": "price", "op": "above", "value": 1}]}
NEVER_ENTRY = {"all": [{"field": "price", "op": "above", "value": 9_999_999}]}


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = str(tmp_path / "engine.db")
    monkeypatch.setenv("DB_PATH", path)
    init_db(path)
    return path


@pytest.fixture
def price_cache():
    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)
    return cache


def _insert_strategy(
    db_file: str,
    *,
    ticker: str = "AAPL",
    status: str = "live",
    entry: dict = ALWAYS_ENTRY,
    exits: dict | None = None,
    sizing: dict | None = None,
    open_qty: float = 0.0,
    open_price: float | None = None,
    opened_at: str | None = None,
    high_water: float | None = None,
    cooldown_until: float | None = None,
    user_id: str = "default",
    name: str = "Engine test",
    created_at: str | None = None,
) -> str:
    strategy_id = str(uuid.uuid4())
    conn = get_conn(db_file)
    try:
        conn.execute(
            """
            INSERT INTO strategies (id, user_id, name, ticker, status, entry,
                exits, sizing, template, created_at, open_qty, open_price,
                opened_at, high_water, cooldown_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                user_id,
                name,
                ticker,
                status,
                json.dumps(entry),
                json.dumps(exits or {}),
                json.dumps(sizing or {"mode": "fixed_qty", "qty": 5}),
                created_at or datetime.now(timezone.utc).isoformat(),
                open_qty,
                open_price,
                opened_at,
                high_water,
                cooldown_until,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return strategy_id


def _insert_position(
    db_file: str, ticker: str, quantity: float, avg_cost: float, user_id="default"
) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user_id,
                ticker,
                quantity,
                avg_cost,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _set_cash(db_file: str, cash: float, user_id="default") -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (cash, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def _row(db_file: str, strategy_id: str):
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
    finally:
        conn.close()


def _query(db_file: str, sql: str, params=()):
    conn = get_conn(db_file)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# exit_reason — the contract-fixed priority (pure function matrix)
# ---------------------------------------------------------------------------


class TestExitReasonPriority:
    def test_stop_loss_wins_over_trailing(self):
        # Price 90: SL (<= 95) and trailing (<= 190 from HW 200) both hit.
        exits = {"stop_loss_pct": 5, "trailing_stop_pct": 5}
        assert exit_reason(exits, 100.0, 200.0, 90.0, None) == "stop_loss"

    def test_trailing_wins_over_take_profit(self):
        # Price 150: trailing (<= 190) and TP (>= 101) both hit — no SL.
        exits = {"trailing_stop_pct": 5, "take_profit_pct": 1}
        assert exit_reason(exits, 100.0, 200.0, 150.0, None) == "trailing_stop"

    def test_take_profit_wins_over_max_holding(self):
        exits = {"take_profit_pct": 1, "max_holding_days": 1}
        opened = _days_ago_iso(5)
        assert exit_reason(exits, 100.0, None, 150.0, opened) == "take_profit"

    def test_max_holding_days_fires_on_calendar_age(self):
        exits = {"max_holding_days": 2}
        assert (
            exit_reason(exits, 100.0, None, 100.0, "2026-01-01T00:00:00+00:00",
                        max_holding_ref=date(2026, 1, 3))
            == "max_holding_days"
        )
        assert (
            exit_reason(exits, 100.0, None, 100.0, "2026-01-01T00:00:00+00:00",
                        max_holding_ref=date(2026, 1, 2))
            is None
        )

    def test_no_exit_when_nothing_triggers(self):
        exits = {"stop_loss_pct": 5, "take_profit_pct": 5}
        assert exit_reason(exits, 100.0, None, 100.0, None) is None


# ---------------------------------------------------------------------------
# entry_quantity — sizing math
# ---------------------------------------------------------------------------


class TestEntryQuantity:
    def test_fixed_qty_passes_through(self):
        assert entry_quantity({"mode": "fixed_qty", "qty": 7.0}, 0.0, 100.0, None) == 7.0

    def test_cash_pct_floors_whole_shares(self):
        # 20% of 10k = 2000; 2000/190 = 10.52... -> 10 shares.
        q = entry_quantity({"mode": "cash_pct", "pct": 20}, 10_000.0, 190.0, None)
        assert q == 10.0

    def test_cash_pct_floors_to_cn_board_lots(self):
        # 20% of 100k = 20000; /140 = 142.8 -> 142 -> lot 100 -> 100.
        q = entry_quantity({"mode": "cash_pct", "pct": 20}, 100_000.0, 140.0, CN_PROFILE)
        assert q == 100.0

    def test_cash_pct_below_one_lot_is_zero(self):
        q = entry_quantity({"mode": "cash_pct", "pct": 20}, 5_000.0, 140.0, CN_PROFILE)
        assert q == 0.0


# ---------------------------------------------------------------------------
# Entry pass — one transaction: trade + chat + snapshot + state
# ---------------------------------------------------------------------------


class TestEngineEntry:
    def test_entry_writes_trade_chat_snapshot_and_state(self, db_file, price_cache):
        sid = _insert_strategy(db_file, sizing={"mode": "fixed_qty", "qty": 5})
        counts = process_strategies_once(db_file, price_cache)
        assert counts["entered"] == 1

        trades = _query(
            db_file, "SELECT * FROM trades WHERE strategy_id = ?", (sid,)
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "buy"
        assert trades[0]["quantity"] == 5

        chats = _query(
            db_file, "SELECT * FROM chat_messages WHERE kind = 'strategy'"
        )
        assert len(chats) == 1
        actions = json.loads(chats[0]["actions"])
        assert actions["strategy_id"] == sid
        assert actions["trades"][0]["status"] == "executed"

        snapshots = _query(db_file, "SELECT * FROM portfolio_snapshots")
        assert len(snapshots) == 1

        row = _row(db_file, sid)
        assert row["open_qty"] == 5
        assert row["open_price"] == trades[0]["price"]
        assert row["opened_at"] is not None
        assert row["high_water"] == trades[0]["price"]
        assert row["entered_count"] == 1
        assert row["cooldown_until"] is None
        assert row["last_fired_at"] is not None

    def test_unmet_entry_condition_skips(self, db_file, price_cache):
        _insert_strategy(db_file, entry=NEVER_ENTRY)
        counts = process_strategies_once(db_file, price_cache)
        assert counts == {"entered": 0, "exited": 0, "skipped": 1, "trade_failed": 0}
        assert _query(db_file, "SELECT * FROM trades") == []

    def test_failed_entry_trade_sets_cooldown(self, db_file, price_cache):
        # 1000 AAPL @ ~190 >> the $10k seed cash -> rejection -> cooldown.
        sid = _insert_strategy(db_file, sizing={"mode": "fixed_qty", "qty": 1000})
        before = time.time()
        counts = process_strategies_once(db_file, price_cache)
        assert counts["trade_failed"] == 1
        row = _row(db_file, sid)
        assert row["open_qty"] == 0
        assert row["cooldown_until"] is not None
        assert row["cooldown_until"] >= before + COOLDOWN_SECONDS - 1

    def test_cash_pct_buys_whole_cn_lot(self, db_file, price_cache):
        _set_cash(db_file, 100_000.0)
        price_cache.update("600519", 140.0)
        sid = _insert_strategy(
            db_file, ticker="600519", sizing={"mode": "cash_pct", "pct": 20}
        )
        counts = process_strategies_once(
            db_file, price_cache, profile=CN_PROFILE
        )
        assert counts["entered"] == 1
        trades = _query(db_file, "SELECT * FROM trades WHERE strategy_id = ?", (sid,))
        assert trades[0]["quantity"] == 100  # 142 shares floored to one lot

    def test_cash_pct_below_one_lot_skips_with_cooldown(self, db_file, price_cache):
        _set_cash(db_file, 5_000.0)
        price_cache.update("600519", 140.0)
        sid = _insert_strategy(
            db_file, ticker="600519", sizing={"mode": "cash_pct", "pct": 20}
        )
        before = time.time()
        counts = process_strategies_once(db_file, price_cache, profile=CN_PROFILE)
        assert counts == {"entered": 0, "exited": 0, "skipped": 1, "trade_failed": 0}
        row = _row(db_file, sid)
        assert row["cooldown_until"] is not None
        assert row["cooldown_until"] >= before + COOLDOWN_SECONDS - 1
        assert _query(db_file, "SELECT * FROM trades") == []

    def test_active_cooldown_skips_and_expired_recovers(self, db_file, price_cache):
        sid = _insert_strategy(
            db_file, cooldown_until=time.time() + 60,
            sizing={"mode": "fixed_qty", "qty": 1},
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["skipped"] == 1
        assert _query(db_file, "SELECT * FROM trades") == []

        # Expire the cooldown -> the next pass enters and clears it.
        conn = get_conn(db_file)
        conn.execute(
            "UPDATE strategies SET cooldown_until = ? WHERE id = ?",
            (time.time() - 5, sid),
        )
        conn.commit()
        conn.close()
        counts = process_strategies_once(db_file, price_cache)
        assert counts["entered"] == 1
        assert _row(db_file, sid)["cooldown_until"] is None

    def test_paused_and_archived_are_never_evaluated(self, db_file, price_cache):
        _insert_strategy(db_file, status="paused")
        _insert_strategy(db_file, status="archived")
        _insert_strategy(db_file, status="draft")
        counts = process_strategies_once(db_file, price_cache)
        assert counts == {"entered": 0, "exited": 0, "skipped": 0, "trade_failed": 0}
        assert _query(db_file, "SELECT * FROM trades") == []


# ---------------------------------------------------------------------------
# Exit pass
# ---------------------------------------------------------------------------


class TestEngineExit:
    def test_stop_loss_exit_sells_and_clears_state(self, db_file, price_cache):
        # AAPL at 190; entered at 250 with a 5% stop -> stop level 237.5.
        _insert_position(db_file, "AAPL", 5, 250.0)
        sid = _insert_strategy(
            db_file,
            exits={"stop_loss_pct": 5, "trailing_stop_pct": 20, "take_profit_pct": 50},
            open_qty=5,
            open_price=250.0,
            opened_at=_days_ago_iso(1),
            high_water=250.0,
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["exited"] == 1

        trades = _query(db_file, "SELECT * FROM trades WHERE strategy_id = ?", (sid,))
        assert len(trades) == 1 and trades[0]["side"] == "sell"
        assert trades[0]["quantity"] == 5

        chats = _query(db_file, "SELECT * FROM chat_messages WHERE kind = 'strategy'")
        assert len(chats) == 1
        # Priority: the gapped-down quote hits SL AND trailing — SL wins.
        assert "stop_loss" in chats[0]["content"]

        row = _row(db_file, sid)
        assert row["open_qty"] == 0
        assert row["open_price"] is None
        assert row["opened_at"] is None
        assert row["high_water"] is None
        assert row["exited_count"] == 1
        assert len(_query(db_file, "SELECT * FROM portfolio_snapshots")) == 1

    def test_trailing_beats_take_profit_in_engine_pass(self, db_file, price_cache):
        # Quote 190: trailing from HW 250 (level 200) and TP from 100 (level
        # 101) both trigger — the sell documents trailing_stop.
        _insert_position(db_file, "AAPL", 5, 100.0)
        _insert_strategy(
            db_file,
            exits={"trailing_stop_pct": 20, "take_profit_pct": 1},
            open_qty=5,
            open_price=100.0,
            opened_at=_days_ago_iso(1),
            high_water=250.0,
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["exited"] == 1
        chats = _query(db_file, "SELECT content FROM chat_messages WHERE kind = 'strategy'")
        assert "trailing_stop" in chats[0]["content"]

    def test_high_water_rises_with_price(self, db_file, price_cache):
        # AAPL 190 > HW 100, trailing level 100*0.9=90 not hit -> skip + raise.
        _insert_position(db_file, "AAPL", 5, 100.0)
        sid = _insert_strategy(
            db_file,
            exits={"trailing_stop_pct": 90},  # level 10 — never triggers here
            open_qty=5,
            open_price=100.0,
            opened_at=_days_ago_iso(1),
            high_water=100.0,
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["skipped"] == 1
        assert _row(db_file, sid)["high_water"] == pytest.approx(190.0)

    def test_max_holding_days_exit(self, db_file, price_cache):
        _insert_position(db_file, "AAPL", 5, 190.0)
        _insert_strategy(
            db_file,
            exits={"max_holding_days": 2},
            open_qty=5,
            open_price=190.0,
            opened_at=_days_ago_iso(3),
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["exited"] == 1
        chats = _query(db_file, "SELECT content FROM chat_messages WHERE kind = 'strategy'")
        assert "max_holding_days" in chats[0]["content"]

    def test_t1_same_day_never_exits_but_high_water_tracks(self, db_file, price_cache):
        # CN T+1: entered TODAY -> every exit is skipped, even a deep stop.
        price_cache.update("600519", 100.0)
        _insert_position(db_file, "600519", 100, 250.0)
        sid = _insert_strategy(
            db_file,
            ticker="600519",
            exits={"stop_loss_pct": 5, "trailing_stop_pct": 10},
            open_qty=100,
            open_price=250.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            high_water=90.0,
        )
        counts = process_strategies_once(db_file, price_cache, profile=CN_PROFILE)
        assert counts == {"entered": 0, "exited": 0, "skipped": 1, "trade_failed": 0}
        assert _query(db_file, "SELECT * FROM trades") == []
        row = _row(db_file, sid)
        assert row["open_qty"] == 100  # untouched
        assert row["high_water"] == pytest.approx(100.0)  # still tracked

    def test_insufficient_shares_clears_state_with_note(self, db_file, price_cache):
        # TP triggered but the user manually sold the shares -> the phantom
        # open state is cleared (no dead loop) and a note is left.
        sid = _insert_strategy(
            db_file,
            exits={"take_profit_pct": 1},
            open_qty=5,
            open_price=100.0,
            opened_at=_days_ago_iso(1),
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts["trade_failed"] == 1
        row = _row(db_file, sid)
        assert row["open_qty"] == 0
        assert row["open_price"] is None
        assert row["exited_count"] == 0  # not a real exit
        chats = _query(db_file, "SELECT * FROM chat_messages WHERE kind = 'strategy'")
        assert len(chats) == 1
        assert "Insufficient shares" in chats[0]["content"]
        assert _query(db_file, "SELECT * FROM trades") == []

    def test_market_closed_rejection_skips_without_state_change(
        self, db_file, price_cache, monkeypatch
    ):
        _insert_position(db_file, "AAPL", 5, 100.0)
        sid = _insert_strategy(
            db_file,
            exits={"take_profit_pct": 1},
            open_qty=5,
            open_price=100.0,
            opened_at=_days_ago_iso(1),
        )
        monkeypatch.setattr(
            strategy_engine,
            "execute_trade_on_conn",
            lambda *a, **k: {"status": "failed", "ticker": "AAPL", "error": "Market closed"},
        )
        counts = process_strategies_once(db_file, price_cache)
        assert counts == {"entered": 0, "exited": 0, "skipped": 1, "trade_failed": 0}
        row = _row(db_file, sid)
        assert row["open_qty"] == 5  # untouched — retried next pass
        assert row["open_price"] == 100.0
        assert _query(db_file, "SELECT * FROM chat_messages WHERE kind = 'strategy'") == []


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestEngineIsolation:
    def test_one_bad_strategy_never_stops_the_pass(
        self, db_file, price_cache, monkeypatch
    ):
        _insert_strategy(
            db_file, ticker="AAPL", created_at="2026-01-01T00:00:00+00:00",
            sizing={"mode": "fixed_qty", "qty": 1},
        )
        good = _insert_strategy(
            db_file, ticker="NVDA", created_at="2026-01-02T00:00:00+00:00",
            sizing={"mode": "fixed_qty", "qty": 1},
        )

        real = strategy_engine.execute_trade_on_conn

        def exploding(conn, cache, ticker, *args, **kwargs):
            if ticker == "AAPL":
                raise RuntimeError("boom")
            return real(conn, cache, ticker, *args, **kwargs)

        monkeypatch.setattr(strategy_engine, "execute_trade_on_conn", exploding)
        counts = process_strategies_once(db_file, price_cache)
        assert counts["entered"] == 1  # NVDA still processed
        trades = _query(db_file, "SELECT * FROM trades")
        assert len(trades) == 1
        assert trades[0]["ticker"] == "NVDA"
        assert trades[0]["strategy_id"] == good

    def test_no_quote_skips(self, db_file, price_cache):
        _insert_strategy(db_file, ticker="ZZZZ")
        counts = process_strategies_once(db_file, price_cache)
        assert counts["skipped"] == 1
