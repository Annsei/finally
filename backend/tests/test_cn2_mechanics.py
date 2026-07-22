"""Unit tests for the field-driven A-share mechanics helpers (CN-2).

These cover ``app.mechanics`` in isolation — the pure functions every route and
loop delegates to. The exact Chinese wording is pinned here (contract §1-§5).
"""

from __future__ import annotations

from app.market.models import PriceUpdate
from app.market.profiles import CN_PROFILE, US_PROFILE
from app.market.session import SessionClock
from app.mechanics import (
    compute_fee,
    lot_size_error,
    market_closed_message,
    order_band_error,
    t1_applies,
    t1_sell_error,
)


class TestLotSizeError:
    def test_cn_buy_non_multiple_rejected_zh(self):
        assert lot_size_error(CN_PROFILE, "buy", 150) == "A股买入须为 100 股的整数倍"

    def test_cn_buy_multiple_ok(self):
        assert lot_size_error(CN_PROFILE, "buy", 200) is None

    def test_cn_sell_any_quantity_ok(self):
        # Odd-lot sells are always legal, even a single share.
        assert lot_size_error(CN_PROFILE, "sell", 37) is None

    def test_us_and_none_never_check(self):
        assert lot_size_error(US_PROFILE, "buy", 150) is None
        assert lot_size_error(None, "buy", 150) is None


class TestComputeFee:
    def test_none_reduces_to_legacy_bps_math(self):
        # No profile: exactly round(notional*bps/1e4) or 0.0 when bps == 0.
        assert compute_fee(10_000.0, "buy", 0.0, None) == 0.0
        assert compute_fee(10_000.0, "buy", 10.0, None) == 10.0

    def test_us_equals_none_for_all_inputs(self):
        for notional in (0.0, 123.45, 10_000.0, 170_000.0):
            for side in ("buy", "sell"):
                for bps in (0.0, 2.5, 10.0):
                    assert compute_fee(notional, side, bps, US_PROFILE) == compute_fee(
                        notional, side, bps, None
                    )

    def test_cn_commission_floor_on_small_notional(self):
        # 1000 * 2.5/1e4 = 0.25 -> floored to the ¥5 minimum commission.
        assert compute_fee(1_000.0, "buy", 2.5, CN_PROFILE) == 5.0

    def test_cn_large_notional_uses_bps(self):
        # 170000 * 2.5/1e4 = 42.5, above the floor, no stamp on a buy.
        assert compute_fee(170_000.0, "buy", 2.5, CN_PROFILE) == 42.5

    def test_cn_stamp_tax_only_on_sell(self):
        # Buy: commission only. Sell: commission + 0.05% stamp.
        buy = compute_fee(100_000.0, "buy", 2.5, CN_PROFILE)
        sell = compute_fee(100_000.0, "sell", 2.5, CN_PROFILE)
        assert buy == 25.0  # 100000*2.5/1e4
        assert sell == round(25.0 + 100_000.0 * 5.0 / 10_000.0, 2)  # + ¥50 stamp
        assert sell - buy == 50.0


class TestT1Applies:
    def test_cn_with_cycling_clock_active(self):
        assert t1_applies(CN_PROFILE, SessionClock(30.0, 10.0)) is True

    def test_cn_with_247_clock_disabled(self):
        # 24/7: no next trading day, so T+1 would never unlock — disabled.
        assert t1_applies(CN_PROFILE, SessionClock()) is False

    def test_cn_without_clock_relies_on_profile(self):
        assert t1_applies(CN_PROFILE, None) is True

    def test_us_and_none_never_apply(self):
        assert t1_applies(US_PROFILE, SessionClock(30.0, 10.0)) is False
        assert t1_applies(None, SessionClock(30.0, 10.0)) is False


class TestT1SellError:
    def test_zh_message_with_sellable_count(self):
        assert (
            t1_sell_error(CN_PROFILE, 100)
            == "T+1：今日买入股份下一交易日方可卖出（当前可卖 100 股）"
        )

    def test_en_message_for_non_zh(self):
        assert "sellable now: 0" in t1_sell_error(US_PROFILE, 0)


class TestMarketClosedMessage:
    def test_zh_for_cn(self):
        assert market_closed_message(CN_PROFILE) == "休市中"

    def test_en_for_us_and_none(self):
        assert market_closed_message(US_PROFILE) == "Market closed"
        assert market_closed_message(None) == "Market closed"


class TestOrderBandError:
    def _quote(self, **kw) -> PriceUpdate:
        base = dict(ticker="300059", price=15.0, previous_price=15.0)
        base.update(kw)
        return PriceUpdate(**base)

    def test_limit_price_above_band_rejected_zh(self):
        quote = self._quote(limit_up=18.0, limit_down=12.0)
        assert (
            order_band_error(CN_PROFILE, quote, 20.0, None)
            == "委托价超出当日涨跌停区间"
        )

    def test_stop_price_below_band_rejected_zh(self):
        quote = self._quote(limit_up=18.0, limit_down=12.0)
        assert (
            order_band_error(CN_PROFILE, quote, None, 10.0)
            == "委托价超出当日涨跌停区间"
        )

    def test_in_band_prices_ok(self):
        quote = self._quote(limit_up=18.0, limit_down=12.0)
        assert order_band_error(CN_PROFILE, quote, 17.0, None) is None

    def test_quote_without_band_is_noop(self):
        # us quotes never carry a band, so the check can never fire.
        assert order_band_error(CN_PROFILE, self._quote(), 999.0, None) is None
        assert order_band_error(US_PROFILE, self._quote(), 999.0, None) is None

    def test_none_profile_noop(self):
        quote = self._quote(limit_up=18.0, limit_down=12.0)
        assert order_band_error(None, quote, 999.0, None) is None
