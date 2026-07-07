"""Daily price-limit band clamp and order-band check (CN-2 §4).

The PriceCache is the single funnel: every tick is clamped into
[limit_down, limit_up] derived from prev_close. PriceUpdate carries the band
only when set (us SSE payload unchanged). place_order rejects resting prices
outside the band; the simulator writes clamped prices back into its GBM state.
"""

from __future__ import annotations

import pytest

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.seed_prices_cn import cn_price_limit_pct
from app.market.simulator import GBMSimulator, SimulatorDataSource
from app.routes.orders import _place_order_on_conn


def _cn_cache() -> PriceCache:
    return PriceCache(limit_pct_fn=cn_price_limit_pct)


class TestClampFunnel:
    def test_first_tick_sets_band_from_prev_close(self):
        cache = _cn_cache()
        u = cache.update("600036", 35.00)  # 招商银行, main board ±10%
        assert u.limit_up == 38.50  # 35 * 1.10
        assert u.limit_down == 31.50  # 35 * 0.90
        assert u.price == 35.00  # first tick within band, unclamped

    def test_tick_above_ceiling_is_clamped(self):
        cache = _cn_cache()
        cache.update("600036", 35.00)  # prev_close 35 -> band [31.5, 38.5]
        u = cache.update("600036", 42.00)  # +20%, above the +10% ceiling
        assert u.price == 38.50  # 封板 at limit_up

    def test_tick_below_floor_is_clamped(self):
        cache = _cn_cache()
        cache.update("600036", 35.00)
        u = cache.update("600036", 20.00)  # crash below the floor
        assert u.price == 31.50  # 跌停 at limit_down

    def test_chinext_uses_20pct_band(self):
        cache = _cn_cache()
        u = cache.update("300059", 15.00)  # ChiNext (30xxxx) -> ±20%
        assert u.limit_up == 18.00  # 15 * 1.20
        assert u.limit_down == 12.00

    def test_bid_ask_also_clamped_into_band(self):
        cache = _cn_cache()
        cache.update("600036", 35.00)
        u = cache.update("600036", 50.00, bid=49.9, ask=50.1)
        assert u.price == 38.50
        assert u.bid <= 38.50
        assert u.ask <= 38.50


class TestEventShockClampedByFunnel:
    def test_burst_shock_over_limit_is_clamped(self):
        """A large explicit tick (as a random event would produce) is bounded."""
        cache = _cn_cache()
        cache.update("601988", 4.50)  # band [4.05, 4.95]
        u = cache.update("601988", 4.50 * 1.5)  # a +50% shock
        assert u.price == 4.95  # clamped to +10% ceiling


class TestToDictConditional:
    def test_cn_quote_carries_band_keys(self):
        cache = _cn_cache()
        payload = cache.update("600036", 35.00).to_dict()
        assert payload["limit_up"] == 38.50
        assert payload["limit_down"] == 31.50

    def test_us_quote_omits_band_keys(self):
        # No limit_pct_fn -> no band -> keys absent, SSE shape unchanged.
        plain = PriceCache()
        payload = plain.update("AAPL", 190.00).to_dict()
        assert "limit_up" not in payload
        assert "limit_down" not in payload


class TestInternalPriceWriteback:
    def test_write_tick_resets_gbm_to_clamped_price(self):
        cache = _cn_cache()
        cache.update("601988", 4.50)  # establish prev_close 4.50
        source = SimulatorDataSource(price_cache=cache, universe=CN_PROFILE.universe)
        sim = GBMSimulator(["601988"], universe=CN_PROFILE.universe)
        sim.set_price("601988", 9.00)  # force a runaway internal price
        source._sim = sim
        source._write_tick("601988", 9.00)  # cache clamps to 4.95
        assert cache.get_price("601988") == 4.95
        # The clamped value was written back into the GBM state (no runaway).
        assert sim.get_price("601988") == 4.95


class TestOrderBandCheck:
    @pytest.fixture
    def env(self, tmp_path):
        db_file = str(tmp_path / "cn_band.db")
        init_db(db_file, seed_cash=CN_PROFILE.seed_cash)
        conn = get_conn(db_file)
        cache = _cn_cache()
        cache.update("600036", 35.00)  # band [31.5, 38.5]
        yield conn, cache
        conn.close()

    def test_limit_price_above_band_rejected(self, env):
        conn, cache = env
        out = _place_order_on_conn(
            conn, cache, ticker="600036", side="buy", quantity=100, kind="limit",
            limit_price=40.00, stop_price=None, time_in_force="gtc", profile=CN_PROFILE,
        )
        assert out["status"] == "failed"
        assert out["error"] == "委托价超出当日涨跌停区间"

    def test_stop_price_below_band_rejected(self, env):
        conn, cache = env
        out = _place_order_on_conn(
            conn, cache, ticker="600036", side="sell", quantity=100, kind="stop",
            limit_price=None, stop_price=30.00, time_in_force="gtc", profile=CN_PROFILE,
        )
        assert out["status"] == "failed"
        assert out["error"] == "委托价超出当日涨跌停区间"

    def test_in_band_limit_rests_ok(self, env):
        conn, cache = env
        out = _place_order_on_conn(
            conn, cache, ticker="600036", side="buy", quantity=100, kind="limit",
            limit_price=33.00, stop_price=None, time_in_force="gtc", profile=CN_PROFILE,
        )
        assert out["status"] == "open"


class TestPrevCloseRollRecomputesBand:
    def test_band_recenters_on_new_prev_close(self):
        cache = _cn_cache()
        cache.update("600036", 35.00)  # prev_close 35 -> band [31.5, 38.5]
        cache.update("600036", 38.00)  # drift up within band; becomes the close
        cache.settle_close(["600036"])
        cache.roll_session(["600036"], timestamp=2000.0)

        rolled = cache.get("600036")
        assert rolled.prev_close == 38.00
        assert rolled.limit_up == round(38.00 * 1.10, 2)  # 41.80
        assert rolled.limit_down == round(38.00 * 0.90, 2)  # 34.20

        # A subsequent over-limit tick now clamps to the NEW ceiling.
        u = cache.update("600036", 99.00, timestamp=2001.0)
        assert u.price == round(38.00 * 1.10, 2)
