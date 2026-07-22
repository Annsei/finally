"""A-share fee model through execute_trade_on_conn (CN-2 §1).

commission = max(min_commission, notional*bps/1e4); stamp = notional*stamp/1e4
on sells only; the total lands in trades.commission and nets realized_pnl.
"""

from __future__ import annotations

import pytest

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.session import SessionClock
from app.routes.portfolio import _execute_trade_on_conn

CN_BPS = 2.5  # profile default commission (万分之 2.5)
# 24/7 clock -> T+1 off (CN-2 §2), isolating the fee math from the sell lock.
NO_T1 = SessionClock()


def _trade(conn, cache, ticker, side, qty):
    return _execute_trade_on_conn(
        conn, cache, ticker, side, qty,
        commission_bps=CN_BPS, session_clock=NO_T1, profile=CN_PROFILE,
    )


@pytest.fixture
def env(tmp_path):
    db_file = str(tmp_path / "cn_fees.db")
    init_db(db_file, seed_cash=CN_PROFILE.seed_cash)
    conn = get_conn(db_file)
    cache = PriceCache()
    cache.update("601988", 5.00)  # 中国银行 — cheap, tests the ¥5 floor
    cache.update("000858", 140.00)  # 五粮液 — big notional, tests bps + stamp
    yield conn, cache, db_file
    conn.close()


def _last_trade(db_file):
    conn = get_conn(db_file)
    try:
        return dict(
            conn.execute(
                "SELECT * FROM trades ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        )
    finally:
        conn.close()


class TestCommissionFloor:
    def test_small_buy_hits_five_yuan_floor(self, env):
        conn, cache, db_file = env
        # 100 * 5.00 = ¥500 notional -> 500*2.5/1e4 = 0.125, floored to ¥5.
        out = _trade(conn, cache, "601988", "buy", 100)
        conn.commit()
        assert out["status"] == "executed"
        assert out["commission"] == 5.0
        assert _last_trade(db_file)["commission"] == 5.0


class TestBpsAboveFloor:
    def test_large_buy_uses_bps(self, env):
        conn, cache, db_file = env
        # 500 * 140 = ¥70,000 -> 70000*2.5/1e4 = ¥17.5 (above the ¥5 floor).
        out = _trade(conn, cache, "000858", "buy", 500)
        conn.commit()
        assert out["commission"] == 17.5


class TestStampTaxSellOnly:
    def test_stamp_added_on_sell_not_buy(self, env):
        conn, cache, db_file = env
        buy = _trade(conn, cache, "000858", "buy", 500)
        conn.commit()
        # Buy: commission only (¥17.5), no stamp.
        assert buy["commission"] == 17.5
        sell = _trade(conn, cache, "000858", "sell", 500)
        conn.commit()
        # Sell notional 70000 -> commission 17.5 + stamp 70000*5/1e4 = 35 -> ¥52.5.
        assert sell["commission"] == 52.5


class TestRealizedPnl:
    def test_realized_pnl_net_of_both_legs(self, env):
        conn, cache, db_file = env
        _trade(conn, cache, "000858", "buy", 500)
        conn.commit()
        # avg_cost folds the ¥17.5 buy commission: (70000+17.5)/500 = 140.035.
        sell = _trade(conn, cache, "000858", "sell", 500)
        conn.commit()
        # Flat price: pnl = (140 - 140.035)*500 - 52.5 = -17.5 - 52.5 = -70.0.
        assert sell["realized_pnl"] == -70.0
