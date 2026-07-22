"""Backtest universe / starting-cash injection (CN-1).

``normalize_backtest_config(universe=...)`` sources the anchor fallback and
GBM params from the universe; ``run_backtest(starting_cash=...)`` shifts the
account baseline while the stats math stays unchanged.
"""

from __future__ import annotations

from app.backtest import STARTING_CASH, normalize_backtest_config, run_backtest
from app.market.cache import PriceCache
from app.market.seed_prices_cn import CN_UNIVERSE

END_TIME = 1_700_000_000.0
SEED = 42


def _normalize(cache: PriceCache | None = None, **overrides) -> dict:
    fields = {
        "ticker": "600519",
        "trigger_type": "price_above",
        "threshold": 1.0,
        "quantity": 1.0,
        "days": 5,
        "seed": SEED,
        "universe": CN_UNIVERSE,
    }
    fields.update(overrides)
    return normalize_backtest_config(cache if cache is not None else PriceCache(), **fields)


class TestNormalizeWithUniverse:
    """Anchor fallback and params lookup ride the injected universe."""

    def test_cn_anchor_from_cn_seed_prices(self):
        outcome = _normalize()
        assert outcome["status"] == "ok"
        assert outcome["config"]["anchor_price"] == 1700.0

    def test_cn_params_embedded_in_config(self):
        outcome = _normalize()
        assert outcome["config"]["params"] == {"sigma": 0.22, "mu": 0.06}

    def test_unknown_cn_params_fall_back_to_default(self):
        cache = PriceCache()
        cache.update("999999", 50.0)  # cached quote, unknown to the universe
        outcome = _normalize(cache, ticker="999999")
        assert outcome["status"] == "ok"
        assert outcome["config"]["params"] == CN_UNIVERSE.default_params

    def test_live_cache_still_beats_the_seed(self):
        cache = PriceCache()
        cache.update("600519", 2000.0)
        outcome = _normalize(cache)
        assert outcome["config"]["anchor_price"] == 2000.0

    def test_us_ticker_unknown_under_cn_universe(self):
        outcome = _normalize(ticker="AAPL")
        assert outcome == {"status": "failed", "ticker": "AAPL", "error": "Ticker not found"}

    def test_no_universe_keeps_legacy_config_shape(self):
        """universe=None: US seed fallback and NO params key (pre-CN-1 shape)."""
        outcome = normalize_backtest_config(
            PriceCache(),
            ticker="AAPL",
            trigger_type="price_above",
            threshold=1.0,
            quantity=1.0,
            seed=SEED,
        )
        assert outcome["status"] == "ok"
        assert outcome["config"]["anchor_price"] == 190.0
        assert "params" not in outcome["config"]


class TestRunBacktestStartingCash:
    """return% / equity / baseline are relative to the injected starting cash."""

    def test_flat_run_lands_on_starting_cash(self):
        """A trigger that never fires: equity == ¥100,000 for the whole run."""
        outcome = _normalize(threshold=1_000_000.0)  # price_above, never fires
        result = run_backtest(
            outcome["config"], commission_bps=0.0, end_time=END_TIME, starting_cash=100_000.0
        )
        assert result["stats"]["fires"] == 0
        assert result["stats"]["final_equity"] == 100_000.0
        assert result["stats"]["total_return_pct"] == 0.0
        assert all(point["value"] == 100_000.0 for point in result["equity_curve"])
        # Baseline buys and holds the same starting cash from the first bar.
        assert result["baseline_curve"][0]["value"] == 100_000.0

    def test_cn_ticker_backtest_fires(self):
        """CN codes are backtestable: an always-true trigger executes entries."""
        outcome = _normalize(trigger_type="price_below", threshold=1_000_000.0)
        result = run_backtest(
            outcome["config"], commission_bps=0.0, end_time=END_TIME, starting_cash=100_000.0
        )
        assert result["stats"]["fires"] >= 1
        assert result["trades"][0]["side"] == "buy"
        # Fill near the ¥1700 anchor — the CN seed drove the GBM path.
        assert 1_000.0 < result["trades"][0]["price"] < 3_000.0

    def test_default_starting_cash_is_unchanged(self):
        """Omitting starting_cash is byte-identical to passing $10,000."""
        outcome = normalize_backtest_config(
            PriceCache(),
            ticker="AAPL",
            trigger_type="price_below",
            threshold=1_000_000.0,
            quantity=1.0,
            days=5,
            seed=SEED,
        )
        default_run = run_backtest(outcome["config"], commission_bps=0.0, end_time=END_TIME)
        explicit_run = run_backtest(
            outcome["config"], commission_bps=0.0, end_time=END_TIME,
            starting_cash=STARTING_CASH,
        )
        assert default_run == explicit_run

    def test_starting_cash_scales_insufficient_cash_rejections(self):
        """¥100k affords one lot of 茅台 where $10k could not."""
        outcome = _normalize(trigger_type="price_below", threshold=1_000_000.0, quantity=50.0)
        poor = run_backtest(
            outcome["config"], commission_bps=0.0, end_time=END_TIME, starting_cash=10_000.0
        )
        rich = run_backtest(
            outcome["config"], commission_bps=0.0, end_time=END_TIME, starting_cash=100_000.0
        )
        # 50 shares at ~¥1700 needs ~¥85k: rejected on $10k, filled on ¥100k.
        assert poor["stats"]["fires"] == 0
        assert poor["stats"]["rejections"]["insufficient_cash"] >= 1
        assert rich["stats"]["fires"] >= 1
