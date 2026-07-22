"""Board-lot (整手) enforcement across trade / order / rule / backtest (CN-2 §3).

A-share buys must be whole multiples of the profile's lot_size (100); sells of
any positive quantity are legal. lot_size 1 (us/None) is always a no-op.
"""

from __future__ import annotations

import pytest

from app.backtest import normalize_backtest_config
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.session import SessionClock
from app.routes.orders import _place_order_on_conn
from app.routes.portfolio import _execute_trade_on_conn
from app.routes.rules import create_rule_on_conn

LOT_MSG = "A股买入须为 100 股的整数倍"
# A 24/7 clock disables T+1 (CN-2 §2) so lot tests can sell in the same session.
NO_T1 = SessionClock()


@pytest.fixture
def cn_env(tmp_path):
    """Fresh DB conn + a cache seeded with two affordable CN tickers."""
    db_file = str(tmp_path / "cn_lot.db")
    init_db(db_file, seed_cash=CN_PROFILE.seed_cash)
    conn = get_conn(db_file)
    cache = PriceCache()
    cache.update("000858", 140.00)  # 五粮液 — 100 shares = ¥14,000
    cache.update("300059", 15.00)  # 东方财富
    yield conn, cache
    conn.close()


class TestTradeLot:
    def test_buy_150_rejected(self, cn_env):
        conn, cache = cn_env
        out = _execute_trade_on_conn(
            conn, cache, "000858", "buy", 150, profile=CN_PROFILE
        )
        assert out["status"] == "failed"
        assert out["error"] == LOT_MSG

    def test_buy_200_ok(self, cn_env):
        conn, cache = cn_env
        out = _execute_trade_on_conn(
            conn, cache, "000858", "buy", 200, profile=CN_PROFILE
        )
        assert out["status"] == "executed"

    def test_sell_37_odd_lot_ok(self, cn_env):
        conn, cache = cn_env
        _execute_trade_on_conn(
            conn, cache, "000858", "buy", 200, session_clock=NO_T1, profile=CN_PROFILE
        )
        out = _execute_trade_on_conn(
            conn, cache, "000858", "sell", 37, session_clock=NO_T1, profile=CN_PROFILE
        )
        assert out["status"] == "executed"


class TestOrderLot:
    def test_limit_buy_non_lot_rejected(self, cn_env):
        conn, cache = cn_env
        out = _place_order_on_conn(
            conn, cache, ticker="000858", side="buy", quantity=150, kind="limit",
            limit_price=100.0, stop_price=None, time_in_force="gtc",
            profile=CN_PROFILE,
        )
        assert out["status"] == "failed"
        assert out["error"] == LOT_MSG

    def test_limit_buy_lot_ok(self, cn_env):
        conn, cache = cn_env
        out = _place_order_on_conn(
            conn, cache, ticker="000858", side="buy", quantity=200, kind="limit",
            limit_price=100.0, stop_price=None, time_in_force="gtc",
            profile=CN_PROFILE,
        )
        assert out["status"] == "open"

    def test_sell_odd_lot_order_ok(self, cn_env):
        conn, cache = cn_env
        out = _place_order_on_conn(
            conn, cache, ticker="000858", side="sell", quantity=37, kind="limit",
            limit_price=999.0, stop_price=None, time_in_force="gtc",
            profile=CN_PROFILE,
        )
        assert out["status"] == "open"


class TestRuleLot:
    def test_buy_rule_non_lot_rejected(self, cn_env):
        conn, cache = cn_env
        out = create_rule_on_conn(
            conn, cache, ticker="000858", trigger_type="price_below", threshold=100.0,
            side="buy", quantity=150, profile=CN_PROFILE,
        )
        assert out["status"] == "failed"
        assert out["error"] == LOT_MSG

    def test_buy_rule_lot_ok(self, cn_env):
        conn, cache = cn_env
        out = create_rule_on_conn(
            conn, cache, ticker="000858", trigger_type="price_below", threshold=100.0,
            side="buy", quantity=200, profile=CN_PROFILE,
        )
        assert out["status"] == "created"


class TestBacktestConfigLot:
    def test_non_lot_quantity_rejected(self, cn_env):
        _, cache = cn_env
        out = normalize_backtest_config(
            cache, ticker="000858", trigger_type="price_below", threshold=100.0,
            quantity=150, profile=CN_PROFILE,
        )
        assert out["status"] == "failed"
        assert out["error"] == LOT_MSG

    def test_lot_quantity_ok(self, cn_env):
        _, cache = cn_env
        out = normalize_backtest_config(
            cache, ticker="000858", trigger_type="price_below", threshold=100.0,
            quantity=200, profile=CN_PROFILE,
        )
        assert out["status"] == "ok"
