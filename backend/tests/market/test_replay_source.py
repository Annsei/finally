"""ReplayDataSource unit tests (D3 contract §1/§5) — fake clock, no sleeps.

The source's loop body is the synchronous ``_step()``, so tests drive it
tick-by-tick against a fake session clock (is_open / session_id attributes)
and crafted daily_bars fixtures. Covers: the pre-window-close seed frame,
open-session tick writes (path prices, incremental volume, compute_quote
bid/ask), the closed-session freeze, session_id-driven day changes, the
loop wrap back to day 0 (prev_close == the PRE-window close), the no-loop
finished freeze, no-coverage tickers ignored (once), suspended days,
single-ticker path failure eviction, and the status snapshot shape.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from app.db.connection import get_conn, init_db
from app.market.cache import PriceCache
from app.market.replay_source import (
    ReplayConfig,
    ReplayDataSource,
    build_day_path,
    replay_seed,
)
from app.market.simulator import compute_quote

PRE = ("2026-05-29", 99.0, 100.0, 98.0, 99.5, 1_000)  # pre-window trading day
DAY0 = ("2026-06-01", 100.0, 104.0, 97.0, 102.0, 50_000)
DAY1 = ("2026-06-02", 103.0, 108.0, 101.0, 107.0, 60_000)
WINDOW = ReplayConfig(
    from_date="2026-06-01",
    to_date="2026-06-02",
    seconds_per_day=4.0,
    break_seconds=2.0,
    loop=True,
)


class FakeClock:
    """Minimal session-clock double: settable is_open / session_id."""

    def __init__(self) -> None:
        self.is_open = True
        self.session_id = 1


def insert_bars(db_path: str, ticker: str, rows, market: str = "us", source: str = "sample"):
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


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "replay.db")
    init_db(path)
    insert_bars(path, "AAPL", [PRE, DAY0, DAY1])
    insert_bars(path, "MSFT", [PRE, DAY0, DAY1])
    return path


def make_source(
    db_path: str,
    clock: FakeClock | None = None,
    config: ReplayConfig = WINDOW,
    cache: PriceCache | None = None,
) -> tuple[ReplayDataSource, PriceCache, FakeClock]:
    price_cache = cache if cache is not None else PriceCache()
    fake_clock = clock if clock is not None else FakeClock()
    source = ReplayDataSource(
        price_cache,
        db_path=db_path,
        market="us",
        session_clock=fake_clock,
        universe=None,
        update_interval=0.5,
        config=config,
    )
    return source, price_cache, fake_clock


async def start_stopped(source: ReplayDataSource, tickers: list[str]) -> None:
    """start() then immediately cancel the loop task — tests drive _step()."""
    await source.start(tickers)
    await source.stop()


@pytest.mark.asyncio
class TestStartAndSeed:
    async def test_seed_frame_is_pre_window_close(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        update = cache.get("AAPL")
        assert update is not None
        assert update.price == 99.5
        assert update.prev_close == 99.5  # first write fixes prev_close

    async def test_no_coverage_ticker_silently_ignored(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL", "NOPE"])
        assert cache.get("NOPE") is None
        assert source.get_tickers() == ["AAPL"]

    async def test_crypto_ticker_absent(self, db_path):
        """Crypto has no daily bars — absent from the cache in replay mode."""
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL", "BTC"])
        assert cache.get("BTC") is None
        assert "BTC" not in source.get_tickers()

    async def test_seed_without_pre_window_bar_falls_back_to_first_open(self, tmp_path):
        path = str(tmp_path / "nopre.db")
        init_db(path)
        insert_bars(path, "AAPL", [DAY0, DAY1])  # window starts at series head
        source, cache, _ = make_source(path)
        await start_stopped(source, ["AAPL"])
        update = cache.get("AAPL")
        assert update.price == DAY0[1]  # first day's open
        assert update.prev_close == DAY0[1]

    async def test_stop_is_idempotent(self, db_path):
        source, _, _ = make_source(db_path)
        await source.start(["AAPL"])
        await source.stop()
        await source.stop()


@pytest.mark.asyncio
class TestOpenSessionTicks:
    async def test_first_step_writes_the_open(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        source._step()
        assert cache.get("AAPL").price == DAY0[1]  # 100.0 open

    async def test_active_path_then_tail_holds_close(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        # 4s day / 0.5s interval => 8 expected ticks, active = int(8*0.9) = 7
        assert source._active_points == 7
        for _ in range(7):
            source._step()
        assert cache.get("AAPL").price == DAY0[4]  # exactly the close
        source._step()  # tail: re-stamps the close at zero volume
        update = cache.get("AAPL")
        assert update.price == DAY0[4]
        assert update.volume == 0.0

    async def test_tick_prices_follow_the_seeded_path(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        bar = {"open": DAY0[1], "high": DAY0[2], "low": DAY0[3], "close": DAY0[4]}
        expected = build_day_path(bar, 7, random.Random(replay_seed("AAPL", DAY0[0])))
        for point in expected:
            source._step()
            assert cache.get("AAPL").price == round(point, 2)

    async def test_ticks_carry_incremental_volume_summing_to_daily_total(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        total = 0.0
        for _ in range(7):
            source._step()
            total += cache.get("AAPL").volume
        assert total == DAY0[5]  # 50,000 conserved

    async def test_bid_ask_use_the_simulator_quote_spread(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        source._step()
        update = cache.get("AAPL")
        bid, ask = compute_quote("AAPL", update.price)
        assert update.bid == bid
        assert update.ask == ask
        assert update.bid < update.price < update.ask

    async def test_day_extremes_bracket_real_high_low(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        for _ in range(7):
            source._step()
        update = cache.get("AAPL")
        assert update.day_high == DAY0[2]
        assert update.day_low == DAY0[3]

    async def test_gap_day_extremes_exclude_prior_close(self, tmp_path):
        """Gap-down day: the seed/roll baseline (prior close 200) must NOT
        linger as day_high — extremes reflect only ticks the replay day
        actually traded (verify finding: explicit day_high/day_low params)."""
        path = str(tmp_path / "gap.db")
        init_db(path)
        insert_bars(
            path,
            "AAPL",
            [
                ("2026-05-29", 199.0, 201.0, 198.0, 200.0, 1_000),  # pre close 200
                ("2026-06-01", 190.0, 192.0, 185.0, 191.0, 10_000),  # gaps down
                ("2026-06-02", 191.0, 193.0, 189.0, 192.0, 12_000),
            ],
        )
        source, cache, _ = make_source(path)
        await start_stopped(source, ["AAPL"])
        assert cache.get("AAPL").price == 200.0  # seed frame = pre-window close
        for _ in range(7):
            source._step()
        update = cache.get("AAPL")
        assert update.day_high == 192.0  # real bar high, not the 200.0 seed
        assert update.day_low == 185.0

    async def test_both_tickers_tick_together(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL", "MSFT"])
        source._step()
        assert cache.get("AAPL").price == DAY0[1]
        assert cache.get("MSFT").price == DAY0[1]

    async def test_replay_is_deterministic_across_sources(self, db_path):
        first, first_cache, _ = make_source(db_path)
        await start_stopped(first, ["AAPL"])
        second, second_cache, _ = make_source(db_path)
        await start_stopped(second, ["AAPL"])
        for _ in range(7):
            first._step()
            second._step()
            assert first_cache.get("AAPL").price == second_cache.get("AAPL").price


@pytest.mark.asyncio
class TestSessionAlignment:
    async def test_closed_session_freezes_writes(self, db_path):
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        source._step()
        clock.is_open = False
        version = cache.version
        source._step()
        source._step()
        assert cache.version == version  # no writes while closed

    async def test_path_resumes_at_same_index_after_midday_pause(self, db_path):
        """CN midday shape: closed phases pause the path, they don't skip it."""
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        source._step()  # tick 0 (open)
        clock.is_open = False  # midday
        source._step()
        source._step()
        clock.is_open = True  # pm resumes — same session_id
        bar = {"open": DAY0[1], "high": DAY0[2], "low": DAY0[3], "close": DAY0[4]}
        expected = build_day_path(bar, 7, random.Random(replay_seed("AAPL", DAY0[0])))
        source._step()  # tick 1 — not tick 3
        assert cache.get("AAPL").price == round(expected[1], 2)

    async def test_session_id_change_advances_to_next_day(self, db_path):
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        for _ in range(8):
            source._step()
        clock.session_id = 2  # reopen: new session
        source._step()
        update = cache.get("AAPL")
        assert update.price == DAY1[1]  # day 1 open
        assert update.prev_close == DAY0[4]  # real previous close
        assert source.snapshot()["current_date"] == DAY1[0]

    async def test_loop_wraps_to_first_day_with_pre_window_prev_close(self, db_path):
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        clock.session_id = 2  # day 1
        source._step()
        clock.session_id = 3  # past the window end -> wrap
        source._step()
        update = cache.get("AAPL")
        assert update.price == DAY0[1]
        assert update.prev_close == PRE[4]  # 99.5 — the PRE-window close
        snap = source.snapshot()
        assert snap["day_index"] == 0
        assert snap["finished"] is False

    async def test_no_loop_freezes_finished(self, db_path):
        config = ReplayConfig(
            from_date="2026-06-01",
            to_date="2026-06-02",
            seconds_per_day=4.0,
            break_seconds=2.0,
            loop=False,
        )
        source, cache, clock = make_source(db_path, config=config)
        await start_stopped(source, ["AAPL"])
        clock.session_id = 2  # day 1
        for _ in range(8):
            source._step()
        assert cache.get("AAPL").price == DAY1[4]  # frozen at the last close
        clock.session_id = 3  # past the end, loop disabled
        version = cache.version
        source._step()
        source._step()
        assert cache.version == version  # no more writes — quotes go stale
        snap = source.snapshot()
        assert snap["finished"] is True
        assert snap["current_date"] == DAY1[0]

    async def test_suspended_day_freezes_only_that_ticker(self, db_path):
        # MSFT has no bar on day 1 (suspension) — AAPL keeps ticking.
        conn = get_conn(db_path)
        conn.execute(
            "DELETE FROM daily_bars WHERE market='us' AND ticker='MSFT' AND date=?",
            (DAY1[0],),
        )
        conn.commit()
        conn.close()
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL", "MSFT"])
        for _ in range(8):
            source._step()
        msft_before = cache.get("MSFT")
        clock.session_id = 2  # day 1
        source._step()
        assert cache.get("AAPL").price == DAY1[1]
        assert cache.get("MSFT").timestamp == msft_before.timestamp  # frozen

    async def test_no_clock_source_never_advances_days(self, db_path):
        source, cache, _ = make_source(db_path)
        source._session_clock = None
        await start_stopped(source, ["AAPL"])
        for _ in range(20):
            source._step()
        assert source.snapshot()["day_index"] == 0
        assert cache.get("AAPL").price == DAY0[4]


@pytest.mark.asyncio
class TestTickerManagement:
    async def test_add_ticker_with_coverage_is_immediately_quotable(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        await source.add_ticker("MSFT")
        assert "MSFT" in source.get_tickers()
        update = cache.get("MSFT")
        assert update is not None
        assert update.prev_close == PRE[4]

    async def test_added_ticker_joins_the_current_tick_index(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        source._step()
        source._step()
        await source.add_ticker("MSFT")
        bar = {"open": DAY0[1], "high": DAY0[2], "low": DAY0[3], "close": DAY0[4]}
        expected = build_day_path(bar, 7, random.Random(replay_seed("MSFT", DAY0[0])))
        source._step()  # tick index 2 for everyone
        assert cache.get("MSFT").price == round(expected[2], 2)

    async def test_add_ticker_without_coverage_ignored_and_logged_once(self, db_path, caplog):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        with caplog.at_level("INFO", logger="app.market.replay_source"):
            await source.add_ticker("NOPE")
            await source.add_ticker("NOPE")  # second add stays silent
        mentions = [r for r in caplog.records if "NOPE" in r.getMessage()]
        assert len(mentions) == 1
        assert cache.get("NOPE") is None
        assert "NOPE" not in source.get_tickers()

    async def test_add_existing_ticker_is_noop(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        version = cache.version
        await source.add_ticker("AAPL")
        assert cache.version == version
        assert source.get_tickers() == ["AAPL"]

    async def test_remove_ticker_evicts_cache(self, db_path):
        source, cache, _ = make_source(db_path)
        await start_stopped(source, ["AAPL", "MSFT"])
        await source.remove_ticker("MSFT")
        assert cache.get("MSFT") is None
        assert source.get_tickers() == ["AAPL"]
        source._step()  # keeps ticking the survivor
        assert cache.get("AAPL").price == DAY0[1]

    async def test_single_ticker_path_failure_evicts_only_that_ticker(
        self, db_path, monkeypatch
    ):
        import app.market.replay_source as replay_module

        real_build = replay_module.build_day_path

        def failing_build(bar, n_points, rng):
            if bar["close"] == DAY0[4] and bar["open"] == DAY0[1]:
                pass  # both tickers share the fixture bar — fail on MSFT below
            return real_build(bar, n_points, rng)

        # Poison only MSFT's day-1 bar so its path build raises.
        conn = get_conn(db_path)
        conn.execute(
            "UPDATE daily_bars SET open='boom' WHERE market='us' AND ticker='MSFT' "
            "AND date=?",
            (DAY1[0],),
        )
        conn.commit()
        conn.close()
        source, cache, clock = make_source(db_path)
        await start_stopped(source, ["AAPL", "MSFT"])
        clock.session_id = 2  # rebuild for day 1 -> MSFT path build fails
        source._step()
        assert "MSFT" not in source.get_tickers()
        assert "AAPL" in source.get_tickers()
        assert cache.get("AAPL").price == DAY1[1]


@pytest.mark.asyncio
class TestLoopAndSnapshot:
    async def test_run_loop_survives_step_exceptions(self, db_path, monkeypatch):
        source, cache, _ = make_source(db_path)
        await source.start(["AAPL"])
        calls = {"n": 0}

        def exploding_step():
            calls["n"] += 1
            raise RuntimeError("boom")

        monkeypatch.setattr(source, "_step", exploding_step)
        await asyncio.sleep(0)  # let the loop run a few iterations
        for _ in range(3):
            await asyncio.sleep(0.05)
        assert not source._task.done()
        assert calls["n"] >= 1
        await source.stop()

    async def test_snapshot_shape_and_values(self, db_path):
        source, _, _ = make_source(db_path)
        await start_stopped(source, ["AAPL"])
        snap = source.snapshot()
        assert snap == {
            "active": True,
            "from": DAY0[0],
            "to": DAY1[0],
            "current_date": DAY0[0],
            "day_index": 0,
            "total_days": 2,
            "seconds_per_day": 4.0,
            "loop": True,
            "finished": False,
            "source_hint": "sample",
        }

    async def test_source_hint_mixed_when_sources_differ(self, tmp_path):
        path = str(tmp_path / "mixed.db")
        init_db(path)
        insert_bars(path, "AAPL", [PRE, DAY0, DAY1], source="sample")
        insert_bars(path, "MSFT", [PRE, DAY0, DAY1], source="yfinance")
        source, _, _ = make_source(path)
        await start_stopped(source, ["AAPL", "MSFT"])
        assert source.snapshot()["source_hint"] == "mixed"

    async def test_source_hint_single_real_source(self, tmp_path):
        path = str(tmp_path / "yf.db")
        init_db(path)
        insert_bars(path, "AAPL", [PRE, DAY0, DAY1], source="yfinance")
        source, _, _ = make_source(path)
        await start_stopped(source, ["AAPL"])
        assert source.snapshot()["source_hint"] == "yfinance"
