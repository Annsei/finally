"""T+1 settlement lock (CN-2 §2): buy locks today, sell frees only prior shares.

Active only with a positive t_plus AND a cycling session clock; 24/7 disables
it. Locked shares release at the next session open (roll_session_open with a
db_path). Applies uniformly to manual, AI (chat), and rule-fired sells.
"""

from __future__ import annotations

import pytest

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.session import SessionClock
from app.routes.portfolio import _execute_trade_on_conn
from app.settlement import roll_session_open


@pytest.fixture
def cn_db(tmp_path):
    db_file = str(tmp_path / "cn_t1.db")
    init_db(db_file, seed_cash=CN_PROFILE.seed_cash)
    return db_file


@pytest.fixture
def cache():
    c = PriceCache()
    c.update("000858", 140.00)  # 五粮液
    return c


def _clock() -> SessionClock:
    """A cycling (non-24/7) session clock, so T+1 is in force."""
    return SessionClock(300.0, 60.0)


def _locked(db_file: str, ticker: str = "000858") -> float:
    conn = get_conn(db_file)
    try:
        row = conn.execute(
            "SELECT t1_locked FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["t1_locked"] if row else 0.0
    finally:
        conn.close()


def _buy(conn, cache, qty, clock, profile=CN_PROFILE):
    return _execute_trade_on_conn(
        conn, cache, "000858", "buy", qty, session_clock=clock, profile=profile
    )


def _sell(conn, cache, qty, clock, profile=CN_PROFILE):
    return _execute_trade_on_conn(
        conn, cache, "000858", "sell", qty, session_clock=clock, profile=profile
    )


class TestBuyLocks:
    def test_buy_locks_todays_shares(self, cn_db, cache):
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, _clock())
            conn.commit()
        finally:
            conn.close()
        assert _locked(cn_db) == 200


class TestSellRejected:
    def test_same_session_sell_rejected_zh(self, cn_db, cache):
        clock = _clock()
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, clock)
            conn.commit()
            out = _sell(conn, cache, 100, clock)
        finally:
            conn.close()
        assert out["status"] == "failed"
        assert (
            out["error"]
            == "T+1：今日买入股份下一交易日方可卖出（当前可卖 0 股）"
        )

    def test_partial_sellable_old_plus_today(self, cn_db, cache):
        """Old 100 (unlocked via roll) + today 100 -> sellable exactly 100."""
        clock = _clock()
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 100, clock)  # locked 100
            conn.commit()
        finally:
            conn.close()
        # Next session opens: prior lock releases.
        roll_session_open(cache, cn_db)
        assert _locked(cn_db) == 0

        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 100, clock)  # today's 100 locks again
            conn.commit()
            assert _locked(cn_db) == 100
            # 200 held, 100 locked -> selling 100 is fine, 101 is not.
            ok = _sell(conn, cache, 100, clock)
            assert ok["status"] == "executed"
            conn.commit()
            bad = _sell(conn, cache, 1, clock)  # remaining 100 are all locked
            assert bad["status"] == "failed"
            assert "当前可卖 0 股" in bad["error"]
        finally:
            conn.close()


class TestRollUnlock:
    def test_roll_releases_all_locks(self, cn_db, cache):
        clock = _clock()
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, clock)
            conn.commit()
        finally:
            conn.close()
        assert _locked(cn_db) == 200
        roll_session_open(cache, cn_db)
        assert _locked(cn_db) == 0

    def test_roll_without_db_path_leaves_locks(self, cn_db, cache):
        clock = _clock()
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, clock)
            conn.commit()
        finally:
            conn.close()
        roll_session_open(cache)  # us/pre-CN-2 signature — no unlock
        assert _locked(cn_db) == 200


class TestT1Disabled247:
    def test_247_mode_no_lock_and_immediate_sell_ok(self, cn_db, cache):
        always_open = SessionClock()  # 24/7
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, always_open)
            conn.commit()
            assert _locked(cn_db) == 0  # nothing locked in 24/7 mode
            out = _sell(conn, cache, 200, always_open)
        finally:
            conn.close()
        assert out["status"] == "executed"


class TestT1AIandRulePaths:
    async def test_ai_chat_trade_subject_to_cn_rules(self, tmp_path, monkeypatch):
        """The chat path threads the profile, so an AI buy obeys 整手 (and,
        by the same wiring, T+1/fees). The LLM_MOCK buys 5 AAPL — a non-lot
        quantity — which the CN profile rejects inside the chat turn."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from app.routes.chat import create_chat_router

        db_file = str(tmp_path / "cn_ai.db")
        monkeypatch.setenv("DB_PATH", db_file)
        monkeypatch.setenv("LLM_MOCK", "true")
        init_db(db_file, seed_cash=CN_PROFILE.seed_cash)

        price_cache = PriceCache()
        price_cache.update("AAPL", 190.0)  # the mock buys 5 AAPL

        app = FastAPI()
        app.include_router(
            create_chat_router(price_cache, db_file, 0.0, _clock(), CN_PROFILE)
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/chat/", json={"message": "buy some"})
            assert resp.status_code == 200
            trade = resp.json()["trades"][0]
            assert trade["status"] == "failed"
            assert trade["error"] == "A股买入须为 100 股的整数倍"

    def test_rule_fired_sell_respects_lock(self, cn_db, cache):
        """A rule firing a sell of today-locked shares fails validation."""
        from app.routes.rules import _fire_rule_if_triggered

        clock = _clock()
        conn = get_conn(cn_db)
        try:
            _buy(conn, cache, 200, clock)
            conn.commit()
            # Insert an active sell rule that triggers immediately (price >= 1).
            conn.execute(
                "INSERT INTO rules (id, user_id, ticker, description, trigger_type, "
                "threshold, side, quantity, status, created_at, last_fired_at, "
                "fire_count) VALUES ('r1', 'default', '000858', 'sell', "
                "'price_above', 1, 'sell', 100, 'active', '2026-01-01', NULL, 0)"
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, user_id, ticker, side, quantity, trigger_type, threshold, "
                "description FROM rules WHERE id = 'r1'"
            ).fetchone()
            # No clock passed to the loop path; CN_PROFILE.t_plus>0 -> T+1 active.
            result = _fire_rule_if_triggered(conn, cache, row, 0.0, CN_PROFILE)
        finally:
            conn.close()
        # Rule consumes itself but the trade fails the T+1 check.
        assert result == "trade_failed"
