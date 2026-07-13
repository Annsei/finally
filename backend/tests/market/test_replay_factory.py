"""FINALLY_LIVE_SOURCE=replay activation matrix (D3 contract §2/§5).

Pins: LIVE_SOURCE_CHOICES gains 'replay' while REAL_DATA_SOURCES does NOT
(the replay source needs the session cycle, never the forced 24/7 clock);
the factory's replay branch (db_path required); the FINALLY_REPLAY_* env
parsing/clamping (bad dates fail startup, seconds clamp with the akshare
warn-and-default precedent, loop boolean spellings); the replay session
clock built from seconds_per_day/break_seconds with the CN midday preserved;
default-env byte regression (replay env vars are inert unless the replay
source is selected); and the startup validation/injection helper
(sample-inject lacking tickers, never overwrite existing rows, explicit
ValueError guidance when the window stays uncoverable, <2 common days
fails).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.db.connection import get_conn, init_db
from app.main import _create_session_clock
from app.market.cache import PriceCache
from app.market.factory import (
    LIVE_SOURCE_CHOICES,
    REAL_DATA_SOURCES,
    create_market_data_source,
    resolve_live_source,
)
from app.market.profiles import CN_PROFILE, US_PROFILE
from app.market.replay_source import (
    DEFAULT_REPLAY_WINDOW_DAYS,
    ReplayConfig,
    ReplayDataSource,
    common_trading_days,
    ensure_replay_startup_data,
    read_replay_env,
    resolve_replay_window,
)
from app.market.simulator import SimulatorDataSource


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


def flat_rows(dates, price=100.0, volume=1000.0):
    return [(d, price, price + 1.0, price - 1.0, price, volume) for d in dates]


class TestChoicesAndResolution:
    def test_choices_include_replay(self):
        assert "replay" in LIVE_SOURCE_CHOICES

    def test_real_data_sources_exclude_replay(self):
        """Replay must NOT force the 24/7 clock — it needs the session cycle."""
        assert REAL_DATA_SOURCES == {"massive", "akshare"}
        assert "replay" not in REAL_DATA_SOURCES

    def test_resolve_passes_replay_through(self):
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": " Replay "}, clear=True):
            assert resolve_live_source() == "replay"

    def test_auto_never_resolves_to_replay(self):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_live_source() == "simulator"
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            assert resolve_live_source() == "massive"

    def test_replay_env_vars_inert_without_replay_source(self):
        """Byte regression: FINALLY_REPLAY_* set but source unset → simulator,
        classic session clock from the regular session env defaults."""
        env = {
            "FINALLY_REPLAY_FROM": "2020-03-09",
            "FINALLY_REPLAY_TO": "2020-03-20",
            "FINALLY_REPLAY_SECONDS_PER_DAY": "60",
        }
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
            assert isinstance(source, SimulatorDataSource)
            clock = _create_session_clock(US_PROFILE)
            assert clock.always_open is False
            # Regular defaults, NOT the replay seconds: 1800s open phase.
            assert clock.next_transition_at() == clock.state_since + 1800.0


class TestReadReplayEnv:
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = read_replay_env()
        assert config == ReplayConfig(
            from_date=None,
            to_date=None,
            seconds_per_day=120.0,
            break_seconds=5.0,
            loop=True,
        )

    def test_explicit_window_parsed_and_normalized(self):
        env = {"FINALLY_REPLAY_FROM": " 2020-03-09 ", "FINALLY_REPLAY_TO": "2020-03-20"}
        with patch.dict(os.environ, env, clear=True):
            config = read_replay_env()
        assert config.from_date == "2020-03-09"
        assert config.to_date == "2020-03-20"

    @pytest.mark.parametrize(
        "env",
        [
            {"FINALLY_REPLAY_FROM": "2020-03-09"},  # only one side set
            {"FINALLY_REPLAY_TO": "2020-03-20"},
        ],
    )
    def test_single_sided_window_fails_startup(self, env):
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="must be set together"):
                read_replay_env()

    @pytest.mark.parametrize("raw", ["2020/03/09", "notadate", "2020-13-40", "3-9-2020"])
    def test_invalid_date_fails_startup(self, raw):
        env = {"FINALLY_REPLAY_FROM": raw, "FINALLY_REPLAY_TO": "2020-03-20"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ISO date"):
                read_replay_env()

    def test_compact_iso_date_normalized(self):
        """Python's fromisoformat accepts the compact ISO form — normalized."""
        env = {"FINALLY_REPLAY_FROM": "20200309", "FINALLY_REPLAY_TO": "20200320"}
        with patch.dict(os.environ, env, clear=True):
            config = read_replay_env()
        assert config.from_date == "2020-03-09"
        assert config.to_date == "2020-03-20"

    def test_from_after_to_fails_startup(self):
        env = {"FINALLY_REPLAY_FROM": "2020-03-20", "FINALLY_REPLAY_TO": "2020-03-09"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="must not be after"):
                read_replay_env()

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", 120.0),  # unset → default
            ("120", 120.0),
            ("30", 30.0),
            ("600", 600.0),
            ("10", 30.0),  # clamped up
            ("0", 30.0),
            ("-5", 30.0),
            ("9999", 600.0),  # clamped down
            ("abc", 120.0),  # unparsable → warn + default
            ("inf", 120.0),  # non-finite → warn + default
            ("nan", 120.0),
        ],
    )
    def test_seconds_per_day_clamped(self, raw, expected):
        env = {"FINALLY_REPLAY_SECONDS_PER_DAY": raw} if raw else {}
        with patch.dict(os.environ, env, clear=True):
            assert read_replay_env().seconds_per_day == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", 5.0),
            ("5", 5.0),
            ("2", 2.0),
            ("60", 60.0),
            ("1", 2.0),  # clamped up
            ("120", 60.0),  # clamped down
            ("junk", 5.0),  # unparsable → warn + default
        ],
    )
    def test_break_seconds_clamped(self, raw, expected):
        env = {"FINALLY_REPLAY_BREAK_SECONDS": raw} if raw else {}
        with patch.dict(os.environ, env, clear=True):
            assert read_replay_env().break_seconds == expected

    @pytest.mark.parametrize("raw", ["false", "FALSE", " 0 ", "no", "off"])
    def test_loop_falsy_spellings(self, raw):
        with patch.dict(os.environ, {"FINALLY_REPLAY_LOOP": raw}, clear=True):
            assert read_replay_env().loop is False

    @pytest.mark.parametrize("raw", ["", "true", "1", "yes", "anything"])
    def test_loop_truthy_spellings(self, raw):
        env = {"FINALLY_REPLAY_LOOP": raw} if raw else {}
        with patch.dict(os.environ, env, clear=True):
            assert read_replay_env().loop is True


class TestFactoryReplayBranch:
    def test_replay_builds_replay_source_with_wiring(self, tmp_path):
        db_path = str(tmp_path / "f.db")
        init_db(db_path)
        cache = PriceCache()
        env = {
            "FINALLY_LIVE_SOURCE": "replay",
            "FINALLY_REPLAY_FROM": "2026-06-01",
            "FINALLY_REPLAY_TO": "2026-06-05",
            "FINALLY_REPLAY_SECONDS_PER_DAY": "45",
            "FINALLY_REPLAY_LOOP": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(
                cache, None, US_PROFILE.universe, db_path=db_path
            )
        assert isinstance(source, ReplayDataSource)
        assert source._cache is cache
        assert source._db_path == db_path
        assert source._market == "us"
        assert source._config.from_date == "2026-06-01"
        assert source._config.seconds_per_day == 45.0
        assert source._config.loop is False

    def test_replay_without_db_path_fails(self):
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": "replay"}, clear=True):
            with pytest.raises(ValueError, match="requires a db_path"):
                create_market_data_source(PriceCache())

    def test_replay_on_cn_profile_uses_cn_market(self, tmp_path):
        db_path = str(tmp_path / "cn.db")
        init_db(db_path)
        env = {"FINALLY_LIVE_SOURCE": "replay", "FINALLY_MARKET": "cn"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(
                PriceCache(), None, CN_PROFILE.universe, db_path=db_path
            )
        assert isinstance(source, ReplayDataSource)
        assert source._market == "cn"


class TestReplaySessionClock:
    def test_replay_clock_uses_replay_seconds(self):
        env = {
            "FINALLY_LIVE_SOURCE": "replay",
            "FINALLY_REPLAY_SECONDS_PER_DAY": "90",
            "FINALLY_REPLAY_BREAK_SECONDS": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            clock = _create_session_clock(US_PROFILE)
        assert clock.always_open is False
        assert clock.phase == "open"  # two-phase us day
        assert clock.next_transition_at() == clock.state_since + 90.0

    def test_replay_clock_overrides_regular_session_env(self):
        env = {
            "FINALLY_LIVE_SOURCE": "replay",
            "FINALLY_SESSION_OPEN_SECONDS": "1800",
            "FINALLY_SESSION_BREAK_SECONDS": "120",
        }
        with patch.dict(os.environ, env, clear=True):
            clock = _create_session_clock(US_PROFILE)
        # Replay defaults (120s day), NOT the regular session env values.
        assert clock.next_transition_at() == clock.state_since + 120.0

    def test_replay_clock_keeps_cn_midday_four_phase(self):
        env = {
            "FINALLY_LIVE_SOURCE": "replay",
            "FINALLY_MARKET": "cn",
            "FINALLY_REPLAY_SECONDS_PER_DAY": "100",
            "FINALLY_REPLAY_BREAK_SECONDS": "8",
        }
        with patch.dict(os.environ, env, clear=True):
            clock = _create_session_clock(CN_PROFILE)
        assert clock.always_open is False
        assert clock.phase == "am"  # four-phase midday day preserved
        # am is half the replay day.
        assert clock.next_transition_at() == clock.state_since + 50.0

    def test_replay_clock_invalid_dates_fail_startup(self):
        env = {
            "FINALLY_LIVE_SOURCE": "replay",
            "FINALLY_REPLAY_FROM": "bogus",
            "FINALLY_REPLAY_TO": "2020-03-20",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ISO date"):
                _create_session_clock(US_PROFILE)


class TestWindowResolution:
    def test_common_days_intersect_across_tickers(self, tmp_path):
        db_path = str(tmp_path / "w.db")
        init_db(db_path)
        insert_bars(db_path, "us", "AAPL", flat_rows(["2026-06-01", "2026-06-02", "2026-06-03"]))
        insert_bars(db_path, "us", "MSFT", flat_rows(["2026-06-02", "2026-06-03", "2026-06-04"]))
        conn = get_conn(db_path)
        try:
            days = common_trading_days(conn, "us", ["AAPL", "MSFT"])
        finally:
            conn.close()
        assert days == ["2026-06-02", "2026-06-03"]

    def test_auto_window_takes_trailing_default_days(self, tmp_path):
        db_path = str(tmp_path / "w2.db")
        init_db(db_path)
        dates = [f"2026-05-{d:02d}" for d in range(1, 29)]  # 28 days
        insert_bars(db_path, "us", "AAPL", flat_rows(dates))
        conn = get_conn(db_path)
        try:
            days = resolve_replay_window(conn, "us", ["AAPL"], ReplayConfig())
        finally:
            conn.close()
        assert len(days) == DEFAULT_REPLAY_WINDOW_DAYS
        assert days[-1] == "2026-05-28"

    def test_explicit_window_bounds_are_inclusive(self, tmp_path):
        db_path = str(tmp_path / "w3.db")
        init_db(db_path)
        dates = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
        insert_bars(db_path, "us", "AAPL", flat_rows(dates))
        config = ReplayConfig(from_date="2026-06-02", to_date="2026-06-03")
        conn = get_conn(db_path)
        try:
            days = resolve_replay_window(conn, "us", ["AAPL"], config)
        finally:
            conn.close()
        assert days == ["2026-06-02", "2026-06-03"]


class TestStartupInjection:
    def test_empty_db_injects_sample_and_resolves_auto_window(self, tmp_path):
        db_path = str(tmp_path / "inject.db")
        init_db(db_path)
        with patch.dict(os.environ, {}, clear=True):
            days = ensure_replay_startup_data(db_path, US_PROFILE)
        assert len(days) == DEFAULT_REPLAY_WINDOW_DAYS
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ticker) AS n, COUNT(DISTINCT source) AS s "
                "FROM daily_bars WHERE market='us'"
            ).fetchone()
            sources = [
                r["source"]
                for r in conn.execute(
                    "SELECT DISTINCT source FROM daily_bars WHERE market='us'"
                )
            ]
        finally:
            conn.close()
        assert row["n"] == 10  # the whole default equity watchlist
        assert sources == ["sample"]  # zero network — sample only

    def test_injection_skips_tickers_with_existing_coverage(self, tmp_path):
        """Real synced rows are never overwritten by the sample injection."""
        db_path = str(tmp_path / "keep.db")
        init_db(db_path)
        real_dates = [f"2026-06-{d:02d}" for d in range(1, 25)]
        insert_bars(db_path, "us", "AAPL", flat_rows(real_dates, price=42.0), source="yfinance")
        with patch.dict(os.environ, {}, clear=True):
            ensure_replay_startup_data(db_path, US_PROFILE)
        conn = get_conn(db_path)
        try:
            aapl_sources = [
                r["source"]
                for r in conn.execute(
                    "SELECT DISTINCT source FROM daily_bars "
                    "WHERE market='us' AND ticker='AAPL'"
                )
            ]
            aapl_price = conn.execute(
                "SELECT close FROM daily_bars WHERE market='us' AND ticker='AAPL' "
                "AND date='2026-06-02'"
            ).fetchone()["close"]
        finally:
            conn.close()
        assert aapl_sources == ["yfinance"]  # untouched
        assert aapl_price == 42.0

    def test_window_outside_sample_range_fails_with_guidance(self, tmp_path):
        db_path = str(tmp_path / "far.db")
        init_db(db_path)
        config = ReplayConfig(from_date="1999-01-01", to_date="1999-01-31")
        with pytest.raises(ValueError) as exc_info:
            ensure_replay_startup_data(db_path, US_PROFILE, config=config)
        message = str(exc_info.value)
        assert "1999-01-01..1999-01-31" in message
        assert "at least 2 common trading days" in message
        assert "/api/market/history/sync" in message
        assert "FINALLY_REPLAY_FROM" in message

    def test_single_common_day_window_fails(self, tmp_path):
        db_path = str(tmp_path / "one.db")
        init_db(db_path)
        for ticker in US_PROFILE.universe.default_watchlist:
            insert_bars(db_path, "us", ticker, flat_rows(["2026-06-01"]))
        config = ReplayConfig(from_date="2026-06-01", to_date="2026-06-01")
        with pytest.raises(ValueError, match="found 1"):
            ensure_replay_startup_data(db_path, US_PROFILE, config=config)

    def test_resolved_window_matches_explicit_env_dates(self, tmp_path):
        db_path = str(tmp_path / "win.db")
        init_db(db_path)
        with patch.dict(os.environ, {}, clear=True):
            ensure_replay_startup_data(db_path, US_PROFILE)  # inject the samples
        conn = get_conn(db_path)
        try:
            all_days = common_trading_days(
                conn, "us", list(US_PROFILE.universe.default_watchlist)
            )
        finally:
            conn.close()
        config = ReplayConfig(from_date=all_days[5], to_date=all_days[9])
        days = ensure_replay_startup_data(db_path, US_PROFILE, config=config)
        assert days == all_days[5:10]

    def test_cn_profile_injects_cn_sample_universe(self, tmp_path):
        db_path = str(tmp_path / "cn.db")
        init_db(db_path)
        with patch.dict(os.environ, {}, clear=True):
            days = ensure_replay_startup_data(db_path, CN_PROFILE)
        assert len(days) == DEFAULT_REPLAY_WINDOW_DAYS
        conn = get_conn(db_path)
        try:
            n = conn.execute(
                "SELECT COUNT(DISTINCT ticker) AS n FROM daily_bars WHERE market='cn'"
            ).fetchone()["n"]
        finally:
            conn.close()
        assert n == len(
            [
                t
                for t in CN_PROFILE.universe.default_watchlist
                if CN_PROFILE.universe.asset_class_for(t) == "equity"
            ]
        )
