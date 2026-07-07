"""Tests for market profile resolution and profile data (CN-1)."""

import logging
import os
from unittest.mock import patch

from app.market.profiles import CN_PROFILE, US_PROFILE, resolve_market_profile
from app.market.seed_prices_cn import CN_UNIVERSE
from app.market.universe import US_UNIVERSE


class TestResolveMarketProfile:
    """FINALLY_MARKET selection: default us, case-insensitive, invalid warns."""

    def test_missing_env_is_us(self):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_market_profile() is US_PROFILE

    def test_empty_env_is_us(self):
        with patch.dict(os.environ, {"FINALLY_MARKET": ""}, clear=True):
            assert resolve_market_profile() is US_PROFILE

    def test_whitespace_env_is_us(self):
        with patch.dict(os.environ, {"FINALLY_MARKET": "   "}, clear=True):
            assert resolve_market_profile() is US_PROFILE

    def test_us_selects_us(self):
        with patch.dict(os.environ, {"FINALLY_MARKET": "us"}, clear=True):
            assert resolve_market_profile() is US_PROFILE

    def test_cn_selects_cn(self):
        with patch.dict(os.environ, {"FINALLY_MARKET": "cn"}, clear=True):
            assert resolve_market_profile() is CN_PROFILE

    def test_case_insensitive(self):
        for raw in ("CN", "Cn", "cN", " cn "):
            with patch.dict(os.environ, {"FINALLY_MARKET": raw}, clear=True):
                assert resolve_market_profile() is CN_PROFILE, raw
        with patch.dict(os.environ, {"FINALLY_MARKET": "US"}, clear=True):
            assert resolve_market_profile() is US_PROFILE

    def test_invalid_warns_and_falls_back_to_us(self, caplog):
        with patch.dict(os.environ, {"FINALLY_MARKET": "jp"}, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.market.profiles"):
                assert resolve_market_profile() is US_PROFILE
        assert any("FINALLY_MARKET" in record.message for record in caplog.records)


class TestUSProfile:
    """The us profile is the pre-CN-1 status quo, verbatim."""

    def test_fields(self):
        assert US_PROFILE.key == "us"
        assert US_PROFILE.currency_symbol == "$"
        assert US_PROFILE.locale == "en-US"
        assert US_PROFILE.lot_size == 1
        assert US_PROFILE.t_plus == 0
        assert US_PROFILE.stamp_tax_bps_sell == 0.0
        assert US_PROFILE.min_commission == 0.0
        assert US_PROFILE.default_commission_bps == 0.0
        assert US_PROFILE.midday_break is False
        assert US_PROFILE.up_is_red is False
        assert US_PROFILE.seed_cash == 10_000.0
        assert US_PROFILE.universe is US_UNIVERSE

    def test_price_limit_is_always_none(self):
        assert US_PROFILE.price_limit_pct("AAPL") is None
        assert US_PROFILE.price_limit_pct("600519") is None
        assert US_PROFILE.price_limit_pct("ZZZZ") is None


class TestCNProfile:
    """The cn profile carries the A-share mechanics as data (enforcement CN-2)."""

    def test_fields(self):
        assert CN_PROFILE.key == "cn"
        assert CN_PROFILE.currency_symbol == "¥"
        assert CN_PROFILE.locale == "zh-CN"
        assert CN_PROFILE.lot_size == 100
        assert CN_PROFILE.t_plus == 1
        assert CN_PROFILE.stamp_tax_bps_sell == 5.0
        assert CN_PROFILE.min_commission == 5.0
        assert CN_PROFILE.default_commission_bps == 2.5
        assert CN_PROFILE.midday_break is True
        assert CN_PROFILE.up_is_red is True
        assert CN_PROFILE.seed_cash == 100_000.0
        assert CN_PROFILE.universe is CN_UNIVERSE

    def test_price_limit_by_board(self):
        assert CN_PROFILE.price_limit_pct("600519") == 10.0
        assert CN_PROFILE.price_limit_pct("300750") == 20.0
        assert CN_PROFILE.price_limit_pct("688981") == 20.0
        assert CN_PROFILE.price_limit_pct("999999") == 10.0
