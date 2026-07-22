"""Tests for session settlement (M3.1): close stamping, day-state roll, DAY expiry."""

from __future__ import annotations

import pytest

from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.session import SessionClock
from app.routes.orders import place_order_on_conn
from app.settlement import roll_session_open, settle_session_close
from tests.conftest import FakeTime


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "settlement.db")
    init_db(path)
    return path


def _walked_cache() -> PriceCache:
    """AAPL walked 190 -> 195 -> 192 (prev_close 190, high 195, low 190) + BTC."""
    cache = PriceCache()
    cache.update("AAPL", 190.0, timestamp=1000.0)
    cache.update("AAPL", 195.0, timestamp=1001.0)
    cache.update("AAPL", 192.0, timestamp=1002.0)
    cache.update("BTC", 65000.0, timestamp=1000.0)
    return cache


def _order_status(db_path: str, order_id: str) -> str:
    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        return row["status"]
    finally:
        conn.close()


def _place(db_path: str, cache: PriceCache, **kwargs) -> dict:
    conn = get_conn(db_path)
    try:
        result = place_order_on_conn(conn, cache, **kwargs)
        assert result.get("status") != "failed", result
        conn.commit()
        return result
    finally:
        conn.close()


class TestPriceCacheSessionRoll:
    """settle_close() stamps the close; roll_session() resets day state."""

    def test_settle_close_stamps_current_price(self):
        cache = _walked_cache()
        closes = cache.settle_close(["AAPL"])
        assert closes == {"AAPL": 192.0}
        # Live quote untouched while closed: still shows the finished session.
        frozen = cache.get("AAPL")
        assert frozen.prev_close == 190.0
        assert frozen.day_high == 195.0
        assert frozen.day_low == 190.0

    def test_settle_close_skips_unknown_tickers(self):
        cache = _walked_cache()
        assert cache.settle_close(["NOPE"]) == {}

    def test_roll_session_resets_day_state_to_settled_close(self):
        cache = _walked_cache()
        cache.settle_close(["AAPL"])
        cache.roll_session(["AAPL"], timestamp=2000.0)
        update = cache.get("AAPL")
        assert update.price == 192.0  # price carries over (frozen close)
        assert update.prev_close == 192.0  # prev_close IS the frozen close
        assert update.day_high == 192.0  # extremes reset
        assert update.day_low == 192.0
        assert update.day_change == 0.0  # new session starts flat
        assert update.day_change_percent == 0.0
        assert update.direction == "flat"
        assert update.timestamp == 2000.0

    def test_day_change_computed_vs_new_prev_close_after_roll(self):
        cache = _walked_cache()
        cache.settle_close(["AAPL"])
        cache.roll_session(["AAPL"], timestamp=2000.0)
        cache.update("AAPL", 196.0, timestamp=2001.0)
        update = cache.get("AAPL")
        assert update.prev_close == 192.0
        assert update.day_change == 4.0
        assert update.day_change_percent == round(4.0 / 192.0 * 100, 4)
        assert update.day_high == 196.0
        assert update.day_low == 192.0

    def test_roll_without_settle_uses_current_price(self):
        cache = _walked_cache()
        cache.roll_session(["AAPL"], timestamp=2000.0)
        update = cache.get("AAPL")
        assert update.prev_close == 192.0
        assert update.day_high == 192.0

    def test_roll_bumps_version_once(self):
        cache = _walked_cache()
        version = cache.version
        cache.roll_session(["AAPL", "MSFT"])  # MSFT unknown — skipped
        assert cache.version == version + 1

    def test_roll_of_unknown_tickers_is_a_noop(self):
        cache = _walked_cache()
        version = cache.version
        cache.roll_session(["NOPE"])
        assert cache.version == version

    def test_remove_clears_stamped_close(self):
        cache = _walked_cache()
        cache.settle_close(["AAPL"])
        cache.remove("AAPL")
        cache.update("AAPL", 200.0, timestamp=3000.0)  # re-added fresh
        cache.roll_session(["AAPL"], timestamp=3001.0)
        assert cache.get("AAPL").prev_close == 200.0  # old 192 stamp is gone


class TestSettleSessionCloseOrders:
    """At close: open equity DAY orders expire; GTC and crypto DAY survive."""

    def test_day_expiry_matrix(self, db_path):
        cache = _walked_cache()
        equity_day = _place(
            db_path, cache, ticker="AAPL", side="buy", quantity=1, kind="limit",
            limit_price=100.0, stop_price=None, time_in_force="day",
        )
        equity_gtc = _place(
            db_path, cache, ticker="AAPL", side="buy", quantity=1, kind="limit",
            limit_price=100.0, stop_price=None, time_in_force="gtc",
        )
        crypto_day = _place(
            db_path, cache, ticker="BTC", side="buy", quantity=0.01, kind="limit",
            limit_price=60000.0, stop_price=None, time_in_force="day",
        )
        equity_day_stop = _place(
            db_path, cache, ticker="AAPL", side="buy", quantity=1, kind="stop",
            limit_price=None, stop_price=500.0, time_in_force="day",
        )

        result = settle_session_close(cache, db_path)

        assert result["expired_orders"] == 2  # both equity DAY orders (any kind)
        assert result["closes"]["AAPL"] == 192.0
        assert "BTC" not in result["closes"]  # crypto is never settled
        assert _order_status(db_path, equity_day["id"]) == "expired"
        assert _order_status(db_path, equity_day_stop["id"]) == "expired"
        assert _order_status(db_path, equity_gtc["id"]) == "open"
        assert _order_status(db_path, crypto_day["id"]) == "open"

    def test_terminal_orders_untouched(self, db_path):
        cache = _walked_cache()
        # Marketable limit fills at placement — terminal 'filled' status.
        filled = _place(
            db_path, cache, ticker="AAPL", side="buy", quantity=1, kind="limit",
            limit_price=500.0, stop_price=None, time_in_force="day",
        )
        assert filled["status"] == "filled"
        result = settle_session_close(cache, db_path)
        assert result["expired_orders"] == 0
        assert _order_status(db_path, filled["id"]) == "filled"

    def test_crypto_day_keeps_24h_expires_at(self, db_path):
        cache = _walked_cache()
        crypto_day = _place(
            db_path, cache, ticker="BTC", side="buy", quantity=0.01, kind="limit",
            limit_price=60000.0, stop_price=None, time_in_force="day",
        )
        assert crypto_day["expires_at"] is not None  # 24h TTL still stamped
        settle_session_close(cache, db_path)
        assert _order_status(db_path, crypto_day["id"]) == "open"


class TestFullSessionCycle:
    """Drive a close -> reopen through the clock and both settlement hooks."""

    def test_prev_close_rolls_and_crypto_untouched(self, db_path):
        cache = _walked_cache()
        fake = FakeTime()
        clock = SessionClock(20.0, 10.0, now=fake)
        btc_before = cache.get("BTC")

        fake.advance(20.0)
        assert clock.tick() == ["close"]
        settle_session_close(cache, db_path)  # what the loop's on_close does

        fake.advance(10.0)
        assert clock.tick() == ["open"]
        assert clock.session_id == 2
        roll_session_open(cache)  # what the loop's on_open does

        rolled = cache.get("AAPL")
        assert rolled.prev_close == 192.0  # yesterday's actual close
        assert rolled.day_high == rolled.day_low == 192.0
        assert cache.get("BTC") is btc_before  # crypto exempt from the roll
