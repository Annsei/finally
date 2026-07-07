"""Market profiles for FinAlly (CN-1) — per-market config resolved at startup.

A ``MarketProfile`` bundles a market's currency/locale, trading-mechanics
data (lot size, T+1, price limits, fees, midday break), seed cash, and its
``MarketUniverse``. In CN-1 the mechanics fields are DATA ONLY — carried on
the profile and exposed via GET /api/market/profile — nothing enforces them
yet (T+1/lot/limit/fee enforcement is CN-2; Chinese prompts are CN-3).

``resolve_market_profile()`` reads FINALLY_MARKET exactly once at app
startup in main.py — the ``_read_commission_bps`` pattern — and the result
is injected into routers and background tasks. Helpers never read the
environment themselves.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from .seed_prices_cn import CN_UNIVERSE, cn_price_limit_pct
from .universe import US_UNIVERSE, MarketUniverse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketProfile:
    """One market's runtime configuration — pure data in CN-1."""

    key: str  # "us" | "cn"
    currency_symbol: str  # "$" | "¥"
    locale: str  # "en-US" | "zh-CN"
    lot_size: int  # Board-lot buy multiple (enforcement: CN-2)
    t_plus: int  # T+N sell lock on buys (enforcement: CN-2)
    stamp_tax_bps_sell: float  # Sell-side stamp tax (enforcement: CN-2)
    min_commission: float  # Commission floor per fill (enforcement: CN-2)
    default_commission_bps: float  # Per-side commission (enforcement: CN-2)
    midday_break: bool  # Lunch-break session split (enforcement: CN-2)
    up_is_red: bool  # CN renders gains red / losses green (frontend: CN-3)
    seed_cash: float  # New-user and season-reset cash
    universe: MarketUniverse
    # Per-ticker daily price-limit percent; None = no limits (us). A callable
    # (not a map) because CN limits are board-prefix rules that must also
    # cover unknown user-added codes.
    price_limit_fn: Callable[[str], float] | None = None

    def price_limit_pct(self, ticker: str) -> float | None:
        """Daily price-limit percent for a ticker; None means no limit."""
        if self.price_limit_fn is None:
            return None
        return self.price_limit_fn(ticker)


US_PROFILE = MarketProfile(
    key="us",
    currency_symbol="$",
    locale="en-US",
    lot_size=1,
    t_plus=0,
    stamp_tax_bps_sell=0.0,
    min_commission=0.0,
    default_commission_bps=0.0,
    midday_break=False,
    up_is_red=False,
    seed_cash=10_000.0,
    universe=US_UNIVERSE,
)

CN_PROFILE = MarketProfile(
    key="cn",
    currency_symbol="¥",
    locale="zh-CN",
    lot_size=100,
    t_plus=1,
    stamp_tax_bps_sell=5.0,  # 印花税 0.05%, sell only
    min_commission=5.0,  # 佣金最低 ¥5
    default_commission_bps=2.5,  # 万2.5, both sides
    midday_break=True,
    up_is_red=True,
    seed_cash=100_000.0,  # $10k cannot buy one lot of 贵州茅台
    universe=CN_UNIVERSE,
    price_limit_fn=cn_price_limit_pct,
)

_PROFILES: dict[str, MarketProfile] = {"us": US_PROFILE, "cn": CN_PROFILE}


def resolve_market_profile() -> MarketProfile:
    """Resolve the active MarketProfile from FINALLY_MARKET (default us).

    Case-insensitive. Missing/empty values select the US profile silently;
    an unrecognized value logs one warning and falls back to US. Called
    ONCE at app startup (main.py's lifespan) — the resolved profile is
    injected everywhere else.
    """
    raw = os.getenv("FINALLY_MARKET", "").strip().lower()
    if not raw:
        return US_PROFILE
    profile = _PROFILES.get(raw)
    if profile is None:
        logger.warning("Invalid FINALLY_MARKET=%r — using 'us'", raw)
        return US_PROFILE
    return profile
