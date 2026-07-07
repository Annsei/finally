"""Tests for the Chinese A-share universe data (CN-1)."""

from app.market.seed_prices_cn import (
    CN_DEFAULT_WATCHLIST,
    CN_NAMES,
    CN_SECTORS,
    CN_SEED_PRICES,
    CN_TICKER_PARAMS,
    CN_UNIVERSE,
    cn_price_limit_pct,
)

EXPECTED_TICKERS = {
    "600519",
    "000858",
    "300750",
    "002594",
    "601012",
    "688981",
    "300059",
    "601318",
    "600036",
    "601988",
    "600900",
    "601899",
    "000333",
    "600276",
}

EXPECTED_SECTORS = {"白酒", "新能源", "半导体", "券商", "金融", "公用", "有色", "家电", "医药"}


class TestCNUniverseCompleteness:
    """The 14-ticker universe per CN_MARKET_PLAN.md §2 — nothing missing."""

    def test_exactly_fourteen_tickers(self):
        assert set(CN_SEED_PRICES) == EXPECTED_TICKERS
        assert len(CN_SEED_PRICES) == 14

    def test_every_ticker_has_params_sector_and_name(self):
        for ticker in CN_SEED_PRICES:
            assert ticker in CN_TICKER_PARAMS, ticker
            assert ticker in CN_SECTORS, ticker
            assert ticker in CN_NAMES, ticker

    def test_params_are_valid_gbm_inputs(self):
        """sigma positive; mu in the plan's 0.03-0.08 band."""
        for ticker, params in CN_TICKER_PARAMS.items():
            assert set(params) == {"sigma", "mu"}, ticker
            assert params["sigma"] > 0, ticker
            assert 0.03 <= params["mu"] <= 0.08, ticker

    def test_sectors_cover_the_plan_set(self):
        assert set(CN_SECTORS.values()) == EXPECTED_SECTORS
        assert CN_UNIVERSE.sector_for("600519") == "白酒"
        assert CN_UNIVERSE.sector_for("300750") == "新能源"
        assert CN_UNIVERSE.sector_for("688981") == "半导体"
        assert CN_UNIVERSE.sector_for("999999") == "other"

    def test_seed_prices_match_the_plan(self):
        assert CN_SEED_PRICES["600519"] == 1700.00
        assert CN_SEED_PRICES["300750"] == 180.00
        assert CN_SEED_PRICES["601988"] == 4.50

    def test_names_are_chinese_display_names(self):
        assert CN_NAMES["600519"] == "贵州茅台"
        assert CN_NAMES["300750"] == "宁德时代"

    def test_default_watchlist_is_the_whole_universe(self):
        assert CN_DEFAULT_WATCHLIST == list(CN_SEED_PRICES)
        assert CN_UNIVERSE.default_watchlist is CN_DEFAULT_WATCHLIST

    def test_no_crypto_in_cn(self):
        assert CN_UNIVERSE.crypto_tickers == frozenset()
        for ticker in CN_SEED_PRICES:
            assert CN_UNIVERSE.asset_class_for(ticker) == "equity"


class TestCNPriceLimits:
    """Board-based daily limits: ChiNext/STAR ±20%, main boards ±10%."""

    def test_chinext_and_star_are_twenty(self):
        assert cn_price_limit_pct("300750") == 20.0
        assert cn_price_limit_pct("688981") == 20.0
        assert cn_price_limit_pct("300059") == 20.0

    def test_main_boards_are_ten(self):
        assert cn_price_limit_pct("600519") == 10.0
        assert cn_price_limit_pct("000858") == 10.0
        assert cn_price_limit_pct("601318") == 10.0

    def test_unknown_codes_fall_back_to_ten(self):
        assert cn_price_limit_pct("999999") == 10.0
        assert cn_price_limit_pct("") == 10.0
        assert cn_price_limit_pct("AAPL") == 10.0

    def test_input_is_stripped(self):
        assert cn_price_limit_pct(" 300750 ") == 20.0


class TestCNCorrelations:
    """白酒 0.7 / 新能源 0.6 / 金融 0.5 / cross 0.3; no independent tickers."""

    def test_baijiu_pair(self):
        assert CN_UNIVERSE.pairwise_correlation("600519", "000858") == 0.7

    def test_new_energy_pairs(self):
        assert CN_UNIVERSE.pairwise_correlation("300750", "002594") == 0.6
        assert CN_UNIVERSE.pairwise_correlation("002594", "601012") == 0.6

    def test_finance_pairs(self):
        assert CN_UNIVERSE.pairwise_correlation("601318", "600036") == 0.5
        assert CN_UNIVERSE.pairwise_correlation("600036", "601988") == 0.5

    def test_cross_group_pairs(self):
        assert CN_UNIVERSE.pairwise_correlation("600519", "300750") == 0.3
        # 券商 (broker) is a sector but NOT a correlation group — cross only.
        assert CN_UNIVERSE.pairwise_correlation("300059", "601318") == 0.3
        assert CN_UNIVERSE.pairwise_correlation("999999", "600519") == 0.3

    def test_no_independent_tickers(self):
        assert CN_UNIVERSE.independent_tickers == frozenset()
