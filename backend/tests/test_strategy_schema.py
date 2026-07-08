"""Tests for the P2 §1 data model: schema, migration, trade attribution, chat kind.

Covers:
- trades.strategy_id migration: pre-P2 trades tables gain the column on
  init_db (via _TRADES_NEW_COLUMNS), existing rows read back NULL, and the
  migration is idempotent
- strategies / backtest_runs tables and their indexes exist after init_db
  (fresh AND pre-existing volumes) with the contracted column sets
- _execute_trade_impl: keyword-only ``strategy_id`` appended after the CN-2
  ``profile`` hook; writes trades.strategy_id; the frozen
  ``_execute_trade_on_conn`` / ``execute_trade_on_conn`` signatures delegate
  with NULL attribution (signatures pinned — see the P2 contract deviation
  note on _execute_trade_on_conn's docstring)
- chat kind 'strategy': accepted by GET /api/chat/ ``kind`` filter, rows are
  returned with their kind, and strategy rows are EXCLUDED from the LLM
  conversation window (like brief/rule/review)
"""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.routes.portfolio import (
    _execute_trade_impl,
    _execute_trade_on_conn,
    execute_trade_on_conn,
)


def _columns(db_file: str, table: str) -> dict[str, sqlite3.Row]:
    conn = get_conn(db_file)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"]: row for row in rows}
    finally:
        conn.close()


def _index_names(db_file: str) -> set[str]:
    conn = get_conn(db_file)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        return {row["name"] for row in rows}
    finally:
        conn.close()


def _make_pre_p2_trades_db(db_file: str) -> None:
    """Create a database whose trades table predates strategy_id (pre-P2)."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            """
            CREATE TABLE trades (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL DEFAULT 'default',
                ticker       TEXT NOT NULL,
                side         TEXT NOT NULL,
                quantity     REAL NOT NULL,
                price        REAL NOT NULL,
                commission   REAL NOT NULL DEFAULT 0,
                realized_pnl REAL,
                executed_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, "
            "commission, realized_pnl, executed_at) VALUES "
            "('old-trade', 'default', 'AAPL', 'buy', 5, 100.0, 0, NULL, "
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()


class TestTradesStrategyIdMigration:
    def test_pre_p2_trades_table_gains_strategy_id(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_p2_trades_db(db_file)
        assert "strategy_id" not in _columns(db_file, "trades")

        init_db(db_file)

        cols = _columns(db_file, "trades")
        assert "strategy_id" in cols
        assert cols["strategy_id"]["notnull"] == 0  # nullable — NULL for non-strategy

    def test_existing_rows_read_back_null(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_p2_trades_db(db_file)
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            row = conn.execute(
                "SELECT strategy_id FROM trades WHERE id = 'old-trade'"
            ).fetchone()
        finally:
            conn.close()
        assert row["strategy_id"] is None

    def test_migration_is_idempotent(self, tmp_path):
        db_file = str(tmp_path / "old.db")
        _make_pre_p2_trades_db(db_file)
        init_db(db_file)
        init_db(db_file)  # second run must not raise or duplicate
        assert list(_columns(db_file, "trades")).count("strategy_id") == 1


class TestStrategyTables:
    def test_fresh_db_has_strategies_table(self, tmp_path):
        db_file = str(tmp_path / "fresh.db")
        init_db(db_file)
        cols = set(_columns(db_file, "strategies"))
        assert cols == {
            "id", "user_id", "name", "ticker", "status", "entry", "exits",
            "sizing", "template", "created_at", "deployed_at", "open_qty",
            "open_price", "opened_at", "high_water", "cooldown_until",
            "entered_count", "exited_count", "last_fired_at",
        }

    def test_fresh_db_has_backtest_runs_table(self, tmp_path):
        db_file = str(tmp_path / "fresh.db")
        init_db(db_file)
        cols = set(_columns(db_file, "backtest_runs"))
        assert cols == {
            "id", "user_id", "strategy_id", "label", "created_at", "config",
            "stats", "equity_curve", "baseline_curve", "trades", "runs_summary",
        }

    def test_status_defaults_to_draft_and_counters_to_zero(self, tmp_path):
        db_file = str(tmp_path / "fresh.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            conn.execute(
                "INSERT INTO strategies (id, user_id, name, ticker, entry, exits, "
                "sizing, created_at) VALUES ('s1', 'default', 'Dip', 'AAPL', "
                "'{}', '{}', '{}', '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
            row = conn.execute("SELECT * FROM strategies WHERE id = 's1'").fetchone()
        finally:
            conn.close()
        assert row["status"] == "draft"
        assert row["open_qty"] == 0
        assert row["entered_count"] == 0
        assert row["exited_count"] == 0

    def test_indexes_exist(self, tmp_path):
        db_file = str(tmp_path / "fresh.db")
        init_db(db_file)
        names = _index_names(db_file)
        assert {
            "idx_strategies_user_status",
            "idx_strategies_status",
            "idx_backtest_runs_user_created",
            "idx_backtest_runs_strategy",
        } <= names

    def test_pre_existing_volume_picks_up_new_tables(self, tmp_path):
        # An old database (pre-P2 trades only) gains both new tables on init_db.
        db_file = str(tmp_path / "old.db")
        _make_pre_p2_trades_db(db_file)
        init_db(db_file)
        assert _columns(db_file, "strategies")
        assert _columns(db_file, "backtest_runs")


class TestExecuteTradeStrategyId:
    def test_impl_signature_appends_keyword_only_strategy_id(self):
        sig = inspect.signature(_execute_trade_impl)
        params = list(sig.parameters.keys())
        assert params == [
            "conn", "price_cache", "ticker", "side", "quantity", "commission_bps",
            "session_clock", "user_id", "profile", "strategy_id",
        ]
        assert sig.parameters["strategy_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["strategy_id"].default is None

    def test_frozen_signatures_unchanged(self):
        # The CN-2 impl form (pinned by tests/test_cn2_parity.py) and the
        # legacy public wrapper both stay exactly as they were — strategy
        # attribution lives only on _execute_trade_impl.
        assert list(inspect.signature(_execute_trade_on_conn).parameters) == [
            "conn", "price_cache", "ticker", "side", "quantity", "commission_bps",
            "session_clock", "user_id", "profile",
        ]
        assert list(inspect.signature(execute_trade_on_conn).parameters) == [
            "conn", "price_cache", "ticker", "side", "quantity", "commission_bps",
            "session_clock", "user_id",
        ]

    @pytest.fixture
    def trade_env(self, tmp_path):
        db_file = str(tmp_path / "trade.db")
        init_db(db_file)
        cache = PriceCache()
        cache.update("AAPL", 100.0)
        conn = get_conn(db_file)
        yield SimpleNamespace(conn=conn, cache=cache)
        conn.close()

    def _strategy_id_of(self, conn, trade_id):
        row = conn.execute(
            "SELECT strategy_id FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return row["strategy_id"]

    def test_impl_writes_strategy_id_on_buy_and_sell(self, trade_env):
        buy = _execute_trade_impl(
            trade_env.conn, trade_env.cache, "AAPL", "buy", 10,
            strategy_id="strat-1",
        )
        assert buy["status"] == "executed"
        trade_env.conn.commit()
        assert self._strategy_id_of(trade_env.conn, buy["trade_id"]) == "strat-1"

        sell = _execute_trade_impl(
            trade_env.conn, trade_env.cache, "AAPL", "sell", 10,
            strategy_id="strat-1",
        )
        assert sell["status"] == "executed"
        assert sell["realized_pnl"] is not None
        trade_env.conn.commit()
        assert self._strategy_id_of(trade_env.conn, sell["trade_id"]) == "strat-1"

    def test_impl_defaults_to_null_attribution(self, trade_env):
        outcome = _execute_trade_impl(trade_env.conn, trade_env.cache, "AAPL", "buy", 1)
        trade_env.conn.commit()
        assert self._strategy_id_of(trade_env.conn, outcome["trade_id"]) is None

    def test_wrappers_write_null_strategy_id(self, trade_env):
        via_impl_wrapper = _execute_trade_on_conn(
            trade_env.conn, trade_env.cache, "AAPL", "buy", 1
        )
        via_public = execute_trade_on_conn(
            trade_env.conn, trade_env.cache, "AAPL", "buy", 1
        )
        trade_env.conn.commit()
        assert self._strategy_id_of(trade_env.conn, via_impl_wrapper["trade_id"]) is None
        assert self._strategy_id_of(trade_env.conn, via_public["trade_id"]) is None

    def test_strategy_id_is_keyword_only(self, trade_env):
        with pytest.raises(TypeError):
            _execute_trade_impl(
                trade_env.conn, trade_env.cache, "AAPL", "buy", 1,
                0.0, None, "default", None, "strat-1",  # positional -> rejected
            )


def _insert_strategy_kind_row(db_file: str, content: str) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO chat_messages "
            "(id, user_id, role, content, actions, kind, created_at) "
            "VALUES ('strategy-note', 'default', 'assistant', ?, ?, 'strategy', "
            "'2000-01-01T00:00:00+00:00')",
            (content, json.dumps({"trades": [], "strategy_id": "s1"})),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
class TestChatKindStrategy:
    async def test_kind_filter_accepts_strategy(self, chat_client):
        _insert_strategy_kind_row(os.environ["DB_PATH"], "strategy fired")
        resp = await chat_client.get("/api/chat/", params={"kind": "strategy"})
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert [m["kind"] for m in messages] == ["strategy"]
        assert messages[0]["content"] == "strategy fired"
        assert messages[0]["actions"] == {"trades": [], "strategy_id": "s1"}

    async def test_unknown_kind_is_400(self, chat_client):
        resp = await chat_client.get("/api/chat/", params={"kind": "bogus"})
        assert resp.status_code == 400
        assert "strategy" in resp.json()["error"]  # listed as a legal kind

    async def test_history_returns_strategy_kind_rows(self, chat_client):
        _insert_strategy_kind_row(os.environ["DB_PATH"], "strategy fired")
        resp = await chat_client.post("/api/chat/", json={"message": "hello"})
        assert resp.status_code == 200
        resp = await chat_client.get("/api/chat/")
        kinds = {m["kind"] for m in resp.json()["messages"]}
        assert {"strategy", "chat"} <= kinds

    async def test_strategy_rows_excluded_from_llm_history(self, chat_client, monkeypatch):
        import litellm

        resp = await chat_client.post("/api/chat/", json={"message": "first message"})
        assert resp.status_code == 200

        _insert_strategy_kind_row(
            os.environ["DB_PATH"], "STRATEGY NOISE: engine fired"
        )

        captured: dict = {}

        def fake_completion(*args, **kwargs):
            captured.update(kwargs)
            content = json.dumps(
                {"message": "ok", "trades": [], "watchlist_changes": []}
            )
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", fake_completion)

        resp = await chat_client.post("/api/chat/", json={"message": "second message"})
        assert resp.status_code == 200

        history_contents = [m["content"] for m in captured["messages"][1:]]
        assert "first message" in history_contents
        assert "STRATEGY NOISE: engine fired" not in history_contents
