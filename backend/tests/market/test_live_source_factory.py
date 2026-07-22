"""FINALLY_LIVE_SOURCE selection matrix (D2 contract §1/§6).

Pins the core invariant: with FINALLY_LIVE_SOURCE unset (auto), source
selection is byte-identical to the pre-D2 behavior (MASSIVE_API_KEY →
Massive, otherwise the GBM simulator — the product default). Real sources
are explicit opt-in; explicit misconfiguration (unknown value, massive
without a key, akshare outside FINALLY_MARKET=cn) fails startup with a
clear error instead of degrading silently. The session clock mirrors the
selection: massive AND akshare force 24/7 (always_open), while an explicit
simulator choice honors the session env config even when a stray
MASSIVE_API_KEY is set.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.main import _create_session_clock
from app.market.akshare_live import AkshareLiveSource
from app.market.cache import PriceCache
from app.market.factory import (
    REAL_DATA_SOURCES,
    _read_akshare_poll_seconds,
    create_market_data_source,
    resolve_live_source,
)
from app.market.massive_client import MassiveDataSource
from app.market.profiles import CN_PROFILE, US_PROFILE
from app.market.session import SessionClock
from app.market.simulator import SimulatorDataSource


class TestResolveLiveSource:
    def test_default_is_auto_simulator_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_live_source() == "simulator"

    def test_default_is_auto_massive_with_key(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            assert resolve_live_source() == "massive"

    @pytest.mark.parametrize("raw", ["", "   ", "auto", " AUTO "])
    def test_auto_spellings_follow_the_key(self, raw):
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": raw}, clear=True):
            assert resolve_live_source() == "simulator"
        with patch.dict(
            os.environ, {"FINALLY_LIVE_SOURCE": raw, "MASSIVE_API_KEY": "k"}, clear=True
        ):
            assert resolve_live_source() == "massive"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("simulator", "simulator"),
            (" Simulator ", "simulator"),
            ("massive", "massive"),
            ("akshare", "akshare"),
            ("AKSHARE", "akshare"),
        ],
    )
    def test_explicit_choices_normalized(self, raw, expected):
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": raw}, clear=True):
            assert resolve_live_source() == expected

    def test_explicit_simulator_wins_over_present_key(self):
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "simulator", "MASSIVE_API_KEY": "k"},
            clear=True,
        ):
            assert resolve_live_source() == "simulator"

    @pytest.mark.parametrize("raw", ["foo", "yfinance", "sim", "0"])
    def test_unknown_value_fails_startup(self, raw):
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": raw}, clear=True):
            with pytest.raises(ValueError, match="FINALLY_LIVE_SOURCE must be one of"):
                resolve_live_source()

    def test_real_data_sources_constant(self):
        assert REAL_DATA_SOURCES == {"massive", "akshare"}


class TestFactoryMatrix:
    def test_auto_no_key_builds_simulator_with_injected_wiring(self):
        """Default-env parity: simulator gets cache/session_clock/universe."""
        cache = PriceCache()
        clock = SessionClock()
        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache, clock, US_PROFILE.universe)
        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_auto_with_key_builds_massive_with_the_key(self):
        cache = PriceCache()
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)
        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"
        assert source._cache is cache

    def test_explicit_simulator_ignores_present_key(self):
        cache = PriceCache()
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "simulator", "MASSIVE_API_KEY": "k"},
            clear=True,
        ):
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)

    def test_explicit_massive_requires_key(self):
        cache = PriceCache()
        with patch.dict(os.environ, {"FINALLY_LIVE_SOURCE": "massive"}, clear=True):
            with pytest.raises(ValueError, match="requires MASSIVE_API_KEY"):
                create_market_data_source(cache)

    def test_explicit_massive_with_key(self):
        cache = PriceCache()
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "massive", "MASSIVE_API_KEY": "abc"},
            clear=True,
        ):
            source = create_market_data_source(cache)
        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "abc"

    def test_akshare_on_cn_builds_live_source(self):
        cache = PriceCache()
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "akshare", "FINALLY_MARKET": "cn"},
            clear=True,
        ):
            source = create_market_data_source(cache, None, CN_PROFILE.universe)
        assert isinstance(source, AkshareLiveSource)
        assert source._cache is cache
        assert source._interval == 15.0  # default poll cadence

    def test_akshare_ignores_stray_massive_key_on_cn(self):
        """A leftover MASSIVE_API_KEY must not hijack an explicit akshare
        choice (and main.py's cn+massive guard is scoped to the RESOLVED
        source, so this combination boots)."""
        cache = PriceCache()
        env = {
            "FINALLY_LIVE_SOURCE": "akshare",
            "FINALLY_MARKET": "cn",
            "MASSIVE_API_KEY": "stray",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_live_source() == "akshare"
            source = create_market_data_source(cache)
        assert isinstance(source, AkshareLiveSource)

    @pytest.mark.parametrize(
        "market_env",
        [
            {},  # unset → us profile
            {"FINALLY_MARKET": "us"},
            {"FINALLY_MARKET": "nope"},  # invalid → us fallback
        ],
    )
    def test_akshare_outside_cn_fails_startup(self, market_env):
        cache = PriceCache()
        env = {"FINALLY_LIVE_SOURCE": "akshare", **market_env}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="FINALLY_MARKET=cn"):
                create_market_data_source(cache)


class TestAksharePollSecondsEnv:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", 15.0),  # unset/empty → default
            ("15", 15.0),
            ("30", 30.0),
            ("5", 5.0),
            ("120", 120.0),
            ("1", 5.0),  # clamped up
            ("0", 5.0),
            ("-3", 5.0),
            ("999", 120.0),  # clamped down
            ("1e2", 100.0),
            ("abc", 15.0),  # unparsable → default
            ("inf", 15.0),  # non-finite → default
            ("nan", 15.0),
        ],
    )
    def test_parse_and_clamp(self, raw, expected):
        env = {"FINALLY_AKSHARE_POLL_SECONDS": raw} if raw else {}
        with patch.dict(os.environ, env, clear=True):
            assert _read_akshare_poll_seconds() == expected

    def test_factory_passes_clamped_interval_through(self):
        cache = PriceCache()
        env = {
            "FINALLY_LIVE_SOURCE": "akshare",
            "FINALLY_MARKET": "cn",
            "FINALLY_AKSHARE_POLL_SECONDS": "2",
        }
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(cache)
        assert isinstance(source, AkshareLiveSource)
        assert source._interval == 5.0


class TestSessionClockMirrorsSelection:
    """The always_open condition mirrors the MASSIVE branch for akshare."""

    def test_default_env_keeps_session_cycle(self):
        with patch.dict(os.environ, {}, clear=True):
            clock = _create_session_clock(US_PROFILE)
        assert clock.always_open is False  # env defaults: 1800s open / 120s break

    def test_auto_massive_key_forces_always_open(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "k"}, clear=True):
            clock = _create_session_clock(US_PROFILE)
        assert clock.always_open is True

    def test_explicit_massive_forces_always_open(self):
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "massive", "MASSIVE_API_KEY": "k"},
            clear=True,
        ):
            clock = _create_session_clock(US_PROFILE)
        assert clock.always_open is True

    def test_akshare_forces_always_open(self):
        env = {"FINALLY_LIVE_SOURCE": "akshare", "FINALLY_MARKET": "cn"}
        with patch.dict(os.environ, env, clear=True):
            clock = _create_session_clock(CN_PROFILE)
        assert clock.always_open is True

    def test_akshare_always_open_even_with_explicit_session_env(self):
        env = {
            "FINALLY_LIVE_SOURCE": "akshare",
            "FINALLY_MARKET": "cn",
            "FINALLY_SESSION_OPEN_SECONDS": "600",
            "FINALLY_SESSION_BREAK_SECONDS": "60",
        }
        with patch.dict(os.environ, env, clear=True):
            clock = _create_session_clock(CN_PROFILE)
        assert clock.always_open is True

    def test_explicit_simulator_with_stray_key_keeps_session_cycle(self):
        """Pre-D2 the key alone forced 24/7; an explicit simulator choice now
        honors the session env config (the clock follows the SELECTED source)."""
        with patch.dict(
            os.environ,
            {"FINALLY_LIVE_SOURCE": "simulator", "MASSIVE_API_KEY": "k"},
            clear=True,
        ):
            clock = _create_session_clock(US_PROFILE)
        assert clock.always_open is False

    def test_cn_profile_midday_break_still_applies_to_simulator(self):
        """The CN four-phase day is untouched when the simulator is selected."""
        with patch.dict(os.environ, {"FINALLY_MARKET": "cn"}, clear=True):
            clock = _create_session_clock(CN_PROFILE)
        assert clock.always_open is False
        assert clock.phase == "am"  # midday-enabled four-phase clock
