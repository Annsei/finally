"""Chinese A-share universe for FinAlly (CN-1) — seed data and parameters.

The 14 representative tickers from planning/CN_MARKET_PLAN.md §2: exchange
numeric codes (SSE 60xxxx, SZSE 00xxxx, ChiNext 30xxxx, STAR 68xxxx) with
Chinese display names, sector grouping, realistic seed prices in ¥, and
per-ticker GBM parameters.

Price-limit percentages (``cn_price_limit_pct``) are board-based DATA ONLY
in CN-1 — carried on the profile and exposed via GET /api/market/profile;
nothing clamps prices yet (enforcement is CN-2).
"""

from __future__ import annotations

from .universe import MarketUniverse

# Realistic seed prices in ¥ (plan §2). Unlike the US universe there is no
# crypto set — A-shares only.
CN_SEED_PRICES: dict[str, float] = {
    "600519": 1700.00,  # 贵州茅台
    "000858": 140.00,  # 五粮液
    "300750": 180.00,  # 宁德时代
    "002594": 250.00,  # 比亚迪
    "601012": 18.00,  # 隆基绿能
    "688981": 45.00,  # 中芯国际
    "300059": 15.00,  # 东方财富
    "601318": 45.00,  # 中国平安
    "600036": 35.00,  # 招商银行
    "601988": 4.50,  # 中国银行
    "600900": 28.00,  # 长江电力
    "601899": 17.00,  # 紫金矿业
    "000333": 75.00,  # 美的集团
    "600276": 45.00,  # 恒瑞医药
}

# Chinese display names — surfaced to the frontend via the profile endpoint
# ("600519 贵州茅台" two-line watchlist rows, CN-3).
CN_NAMES: dict[str, str] = {
    "600519": "贵州茅台",
    "000858": "五粮液",
    "300750": "宁德时代",
    "002594": "比亚迪",
    "601012": "隆基绿能",
    "688981": "中芯国际",
    "300059": "东方财富",
    "601318": "中国平安",
    "600036": "招商银行",
    "601988": "中国银行",
    "600900": "长江电力",
    "601899": "紫金矿业",
    "000333": "美的集团",
    "600276": "恒瑞医药",
}

CN_SECTORS: dict[str, str] = {
    "600519": "白酒",
    "000858": "白酒",
    "300750": "新能源",
    "002594": "新能源",
    "601012": "新能源",
    "688981": "半导体",
    "300059": "券商",
    "601318": "金融",
    "600036": "金融",
    "601988": "金融",
    "600900": "公用",
    "601899": "有色",
    "000333": "家电",
    "600276": "医药",
}

# Per-ticker GBM parameters: sigma from plan §2; mu picked in the 0.03-0.08
# band (higher for the growth names, lower for banks/utilities).
CN_TICKER_PARAMS: dict[str, dict[str, float]] = {
    "600519": {"sigma": 0.22, "mu": 0.06},
    "000858": {"sigma": 0.28, "mu": 0.05},
    "300750": {"sigma": 0.35, "mu": 0.08},
    "002594": {"sigma": 0.32, "mu": 0.07},
    "601012": {"sigma": 0.38, "mu": 0.05},
    "688981": {"sigma": 0.42, "mu": 0.07},
    "300059": {"sigma": 0.40, "mu": 0.06},
    "601318": {"sigma": 0.20, "mu": 0.04},
    "600036": {"sigma": 0.18, "mu": 0.04},
    "601988": {"sigma": 0.12, "mu": 0.03},
    "600900": {"sigma": 0.14, "mu": 0.04},
    "601899": {"sigma": 0.30, "mu": 0.06},
    "000333": {"sigma": 0.24, "mu": 0.05},
    "600276": {"sigma": 0.28, "mu": 0.05},
}

# Unknown/user-added CN tickers — same moderate defaults as the US universe.
CN_DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# The CN default watchlist is the entire 14-ticker universe.
CN_DEFAULT_WATCHLIST: list[str] = list(CN_SEED_PRICES)

# Intra-group correlations (plan §2): 白酒 move together strongest, then
# 新能源, then 金融; everything else (半导体/券商/公用/有色/家电/医药 and
# unknown codes) correlates at the cross-group level. No independent
# tickers — the CN universe has no TSLA analogue.
CN_INTRA_BAIJIU_CORR = 0.7
CN_INTRA_NEW_ENERGY_CORR = 0.6
CN_INTRA_FINANCE_CORR = 0.5
CN_CROSS_GROUP_CORR = 0.3

# Daily price-limit percent by board prefix: ChiNext (30xxxx) and STAR
# (68xxxx) trade at ±20%, main boards at ±10%.
CN_BOARD_LIMIT_PCT: dict[str, float] = {"30": 20.0, "68": 20.0}
CN_DEFAULT_LIMIT_PCT: float = 10.0


def cn_price_limit_pct(ticker: str) -> float:
    """Daily price-limit percent for an A-share code, by board prefix.

    ``30xxxx``/``68xxxx`` -> 20.0, everything else — including unknown
    codes — falls back to 10.0. Data only in CN-1 (no clamping).
    """
    return CN_BOARD_LIMIT_PCT.get(ticker.strip()[:2], CN_DEFAULT_LIMIT_PCT)


CN_UNIVERSE = MarketUniverse(
    seed_prices=CN_SEED_PRICES,
    ticker_params=CN_TICKER_PARAMS,
    default_params=CN_DEFAULT_PARAMS,
    default_watchlist=CN_DEFAULT_WATCHLIST,
    sectors=CN_SECTORS,
    names=CN_NAMES,
    crypto_tickers=frozenset(),
    correlation_groups={
        "白酒": frozenset({"600519", "000858"}),
        "新能源": frozenset({"300750", "002594", "601012"}),
        "金融": frozenset({"601318", "600036", "601988"}),
    },
    group_correlations={
        "白酒": CN_INTRA_BAIJIU_CORR,
        "新能源": CN_INTRA_NEW_ENERGY_CORR,
        "金融": CN_INTRA_FINANCE_CORR,
    },
    cross_group_corr=CN_CROSS_GROUP_CORR,
)
