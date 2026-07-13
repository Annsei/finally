"""Replay <-> settlement integration (D3 contract §5) — REAL SessionClock.

The contract's core invariant, verified end-to-end with the UNTOUCHED
settlement machinery (settle_session_close / roll_session_open /
PriceCache.settle_close / roll_session): because each replay day's last
written tick is exactly the day's real close, the existing close stamp IS
the real close, and after the roll ``prev_close`` is the REAL previous
close — so day_change_percent is the real day change and the CN price-limit
band is exactly 真实昨收 ± pct. Also pins the loop wrap through settle/roll
(prev_close = the PRE-window close) and the CN four-phase midday day.

Time is injected (FakeTime) and the session-clock loop is driven manually —
no sleeps, deterministic transitions.
"""

from __future__ import annotations

import pytest

from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.replay_source import ReplayConfig, ReplayDataSource
from app.market.session import SessionClock
from app.market.simulator import compute_quote
from app.routes.portfolio import _execute_trade_on_conn
from app.settlement import roll_session_open, settle_session_close
from tests.conftest import FakeTime

PRE = ("2026-05-29", 99.0, 100.0, 98.0, 99.5, 1_000)
DAY0 = ("2026-06-01", 100.0, 104.0, 97.0, 102.0, 50_000)
DAY1 = ("2026-06-02", 103.0, 108.0, 101.0, 107.0, 60_000)

# CN fixture: 主板 ticker (±10% band), prices scaled to survive the band.
CN_PRE = ("2026-05-29", 100.0, 101.0, 99.0, 100.0, 10_000)
CN_DAY0 = ("2026-06-01", 101.0, 106.0, 98.0, 105.0, 20_000)
CN_DAY1 = ("2026-06-02", 106.0, 112.0, 104.0, 110.0, 30_000)

OPEN_SECONDS = 4.0
BREAK_SECONDS = 2.0


def insert_bars(db_path, market, ticker, rows, source="sample"):
    conn = get_conn(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_bars (market, ticker, date, open, high, "
            "low, close, volume, source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(market, ticker, d, o, h, low, c, v, source, "x") for d, o, h, low, c, v in rows],
        )
        conn.commit()
    finally:
        conn.close()


def drive_transitions(clock: SessionClock, cache: PriceCache, db_path: str) -> list[str]:
    """One session-clock-loop iteration: tick + the main.py settle/roll hooks."""
    events = clock.tick()
    for event in events:
        if event == "close":
            settle_session_close(cache, db_path)
        elif event == "open":
            roll_session_open(cache, db_path)
    return events


async def replay_day(source: ReplayDataSource, ticks: int = 9) -> None:
    """Write a full open window's worth of ticks (active path + tail)."""
    for _ in range(ticks):
        source._step()


@pytest.fixture
def us_setup(tmp_path):
    db_path = str(tmp_path / "us.db")
    init_db(db_path)
    insert_bars(db_path, "us", "AAPL", [PRE, DAY0, DAY1])
    fake_time = FakeTime()
    clock = SessionClock(OPEN_SECONDS, BREAK_SECONDS, now=fake_time)
    cache = PriceCache()
    config = ReplayConfig(
        from_date=DAY0[0],
        to_date=DAY1[0],
        seconds_per_day=OPEN_SECONDS,
        break_seconds=BREAK_SECONDS,
        loop=True,
    )
    source = ReplayDataSource(
        cache,
        db_path=db_path,
        market="us",
        session_clock=clock,
        universe=None,
        update_interval=0.5,
        config=config,
    )
    return db_path, fake_time, clock, cache, source


@pytest.mark.asyncio
class TestUsSettlementIntegration:
    async def test_two_day_replay_rolls_real_prev_close(self, us_setup):
        db_path, fake_time, clock, cache, source = us_setup
        await source.start(["AAPL"])
        await source.stop()

        await replay_day(source)  # day 0 completes at the real close
        assert cache.get("AAPL").price == DAY0[4]

        fake_time.advance(OPEN_SECONDS)
        assert drive_transitions(clock, cache, db_path) == ["close"]
        source._step()  # closed — frozen
        fake_time.advance(BREAK_SECONDS)
        assert drive_transitions(clock, cache, db_path) == ["open"]

        # The UNTOUCHED roll machinery has already stamped the real close.
        update = cache.get("AAPL")
        assert update.prev_close == DAY0[4]

        source._step()  # first day-1 tick
        update = cache.get("AAPL")
        assert update.price == DAY1[1]
        assert update.prev_close == DAY0[4]  # 真实昨收
        assert update.day_change_percent == round(
            (DAY1[1] - DAY0[4]) / DAY0[4] * 100, 4
        )

    async def test_settle_stamp_is_the_real_close(self, us_setup):
        db_path, fake_time, clock, cache, source = us_setup
        await source.start(["AAPL"])
        await source.stop()
        await replay_day(source)
        fake_time.advance(OPEN_SECONDS)
        clock.tick()
        result = settle_session_close(cache, db_path)
        assert result["closes"]["AAPL"] == DAY0[4]

    async def test_loop_wrap_passes_through_settle_roll_with_pre_window_close(
        self, us_setup
    ):
        db_path, fake_time, clock, cache, source = us_setup
        await source.start(["AAPL"])
        await source.stop()

        for _ in range(2):  # day 0 and day 1
            await replay_day(source)
            fake_time.advance(OPEN_SECONDS)
            drive_transitions(clock, cache, db_path)
            fake_time.advance(BREAK_SECONDS)
            drive_transitions(clock, cache, db_path)
            source._step()

        # Wrapped back to day 0: prev_close is the PRE-window close, not the
        # settled last-day close — the explicit-prev_close write wins.
        update = cache.get("AAPL")
        assert source.snapshot()["day_index"] == 0
        assert update.price == DAY0[1]
        assert update.prev_close == PRE[4]

    async def test_finished_replay_keeps_settling_flat(self, tmp_path):
        db_path = str(tmp_path / "noloop.db")
        init_db(db_path)
        insert_bars(db_path, "us", "AAPL", [PRE, DAY0, DAY1])
        fake_time = FakeTime()
        clock = SessionClock(OPEN_SECONDS, BREAK_SECONDS, now=fake_time)
        cache = PriceCache()
        config = ReplayConfig(
            from_date=DAY0[0],
            to_date=DAY1[0],
            seconds_per_day=OPEN_SECONDS,
            break_seconds=BREAK_SECONDS,
            loop=False,
        )
        source = ReplayDataSource(
            cache,
            db_path=db_path,
            market="us",
            session_clock=clock,
            universe=None,
            update_interval=0.5,
            config=config,
        )
        await source.start(["AAPL"])
        await source.stop()
        for _ in range(3):  # day 0, day 1, then past the end
            await replay_day(source)
            fake_time.advance(OPEN_SECONDS)
            drive_transitions(clock, cache, db_path)
            fake_time.advance(BREAK_SECONDS)
            drive_transitions(clock, cache, db_path)
            source._step()
        update = cache.get("AAPL")
        assert source.snapshot()["finished"] is True
        assert update.price == DAY1[4]  # frozen at the real last close
        # The settle/roll cycle kept running on the frozen price: flat day.
        assert update.prev_close == DAY1[4]
        assert update.day_change_percent == 0.0


@pytest.mark.asyncio
class TestCnSettlementIntegration:
    async def test_cn_limit_band_follows_real_prev_close(self, tmp_path):
        db_path = str(tmp_path / "cn.db")
        init_db(db_path)
        insert_bars(db_path, "cn", "600519", [CN_PRE, CN_DAY0, CN_DAY1])
        fake_time = FakeTime()
        # CN four-phase day: am + midday + pm + closed (midday == break len).
        clock = SessionClock(
            OPEN_SECONDS,
            BREAK_SECONDS,
            now=fake_time,
            midday_break_seconds=BREAK_SECONDS,
        )
        cache = PriceCache(limit_pct_fn=CN_PROFILE.price_limit_pct)
        config = ReplayConfig(
            from_date=CN_DAY0[0],
            to_date=CN_DAY1[0],
            seconds_per_day=OPEN_SECONDS,
            break_seconds=BREAK_SECONDS,
            loop=True,
        )
        source = ReplayDataSource(
            cache,
            db_path=db_path,
            market="cn",
            session_clock=clock,
            universe=None,
            update_interval=0.5,
            config=config,
        )
        await source.start(["600519"])
        await source.stop()

        # Day 0 band derives from the pre-window close (seed write).
        update = cache.get("600519")
        assert update.limit_up == round(CN_PRE[4] * 1.10, 2)
        assert update.limit_down == round(CN_PRE[4] * 0.90, 2)

        # Walk the four-phase day: am -> midday -> pm, path splices across.
        source._step()  # am tick
        fake_time.advance(OPEN_SECONDS / 2)
        assert clock.tick() == ["midday"]  # no settle/roll hooks at midday
        source._step()  # frozen during lunch
        fake_time.advance(BREAK_SECONDS)
        assert clock.tick() == ["resume"]
        await replay_day(source)  # finish day 0 in the pm half
        assert cache.get("600519").price == CN_DAY0[4]

        fake_time.advance(OPEN_SECONDS / 2)
        assert drive_transitions(clock, cache, db_path) == ["close"]
        fake_time.advance(BREAK_SECONDS)
        assert drive_transitions(clock, cache, db_path) == ["open"]

        source._step()  # first day-1 tick
        update = cache.get("600519")
        # 涨跌停带 == 真实昨收 ± 10% (主板), against day 0's REAL close.
        assert update.prev_close == CN_DAY0[4]
        assert update.limit_up == round(CN_DAY0[4] * 1.10, 2)
        assert update.limit_down == round(CN_DAY0[4] * 0.90, 2)
        assert update.day_change_percent == round(
            (update.price - CN_DAY0[4]) / CN_DAY0[4] * 100, 4
        )

    async def test_cn_lot_trades_fill_at_replay_prices(self, tmp_path):
        """整手交易在回放价上照常 (contract §5) — UNTOUCHED trade machinery.

        Against a CN ReplayDataSource feeding the shared PriceCache: a
        100-share board-lot buy fills at the CURRENT replay quote (ask side
        of the path tick), an odd-lot buy is rejected with the CN-2 message,
        a lunch-break order is rejected, and the pm-half fill follows the
        advanced path tick — all inside the price-limit band derived from
        the real prev close. FakeTime, zero network.
        """
        db_path = str(tmp_path / "cn3.db")
        init_db(db_path, seed_cash=CN_PROFILE.seed_cash)
        insert_bars(db_path, "cn", "600519", [CN_PRE, CN_DAY0, CN_DAY1])
        fake_time = FakeTime()
        clock = SessionClock(
            OPEN_SECONDS,
            BREAK_SECONDS,
            now=fake_time,
            midday_break_seconds=BREAK_SECONDS,
        )
        cache = PriceCache(limit_pct_fn=CN_PROFILE.price_limit_pct)
        config = ReplayConfig(
            from_date=CN_DAY0[0],
            to_date=CN_DAY1[0],
            seconds_per_day=OPEN_SECONDS,
            break_seconds=BREAK_SECONDS,
            loop=True,
        )
        source = ReplayDataSource(
            cache,
            db_path=db_path,
            market="cn",
            session_clock=clock,
            universe=None,
            update_interval=0.5,
            config=config,
        )
        await source.start(["600519"])
        await source.stop()

        conn = get_conn(db_path)
        try:
            source._step()  # am tick 0 — the replay day-0 open
            update = cache.get("600519")
            assert update.price == CN_DAY0[1]

            # 整手 (100-share) buy fills at the CURRENT replay quote: the
            # deterministic ask around the path tick the source just wrote.
            out = _execute_trade_on_conn(
                conn, cache, "600519", "buy", 100,
                session_clock=clock, profile=CN_PROFILE,
            )
            assert out["status"] == "executed"
            assert out["price"] == update.ask
            assert update.ask == compute_quote("600519", CN_DAY0[1])[1]
            # Fill sits inside the band from the REAL prev close (主板 ±10%).
            assert update.limit_down <= out["price"] <= update.limit_up

            # 非整手 buy rejects exactly as in live mode.
            odd = _execute_trade_on_conn(
                conn, cache, "600519", "buy", 50,
                session_clock=clock, profile=CN_PROFILE,
            )
            assert odd["status"] == "failed"
            assert odd["error"] == "A股买入须为 100 股的整数倍"

            # Lunch break: market orders reject while the path is frozen.
            fake_time.advance(OPEN_SECONDS / 2)
            assert clock.tick() == ["midday"]
            lunch = _execute_trade_on_conn(
                conn, cache, "600519", "buy", 100,
                session_clock=clock, profile=CN_PROFILE,
            )
            assert lunch["status"] == "failed"

            # pm resumes seamlessly — the fill follows the advanced tick.
            fake_time.advance(BREAK_SECONDS)
            assert clock.tick() == ["resume"]
            source._step()  # pm tick 1
            update = cache.get("600519")
            pm = _execute_trade_on_conn(
                conn, cache, "600519", "buy", 100,
                session_clock=clock, profile=CN_PROFILE,
            )
            assert pm["status"] == "executed"
            assert pm["price"] == update.ask
            assert update.ask == compute_quote("600519", update.price)[1]
            assert update.limit_down <= pm["price"] <= update.limit_up

            row = conn.execute(
                "SELECT quantity FROM positions "
                "WHERE user_id = 'default' AND ticker = '600519'"
            ).fetchone()
            assert row["quantity"] == 200
        finally:
            conn.close()

    async def test_cn_midday_pause_does_not_consume_path_ticks(self, tmp_path):
        db_path = str(tmp_path / "cn2.db")
        init_db(db_path)
        insert_bars(db_path, "cn", "600519", [CN_PRE, CN_DAY0, CN_DAY1])
        fake_time = FakeTime()
        clock = SessionClock(
            OPEN_SECONDS,
            BREAK_SECONDS,
            now=fake_time,
            midday_break_seconds=BREAK_SECONDS,
        )
        cache = PriceCache(limit_pct_fn=CN_PROFILE.price_limit_pct)
        config = ReplayConfig(
            from_date=CN_DAY0[0],
            to_date=CN_DAY1[0],
            seconds_per_day=OPEN_SECONDS,
            break_seconds=BREAK_SECONDS,
            loop=True,
        )
        source = ReplayDataSource(
            cache,
            db_path=db_path,
            market="cn",
            session_clock=clock,
            universe=None,
            update_interval=0.5,
            config=config,
        )
        await source.start(["600519"])
        await source.stop()

        source._step()  # am: tick 0 (the open)
        assert cache.get("600519").price == CN_DAY0[1]
        fake_time.advance(OPEN_SECONDS / 2)
        clock.tick()  # midday
        version = cache.version
        source._step()
        assert cache.version == version  # paused, tick index NOT consumed
        fake_time.advance(BREAK_SECONDS)
        clock.tick()  # resume (same session_id)
        assert clock.session_id == 1
        # Remaining active path (7 points total) + tail completes at close.
        await replay_day(source, ticks=8)
        assert cache.get("600519").price == CN_DAY0[4]
