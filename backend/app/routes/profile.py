"""Market profile API route for FinAlly (CN-1).

Provides:
- GET /api/market/profile — the active market's runtime configuration, the
  frontend's single source of market truth (contract:
  planning/CN1_PROFILE_CONTRACT.md §5). The payload is fixed at startup:

    {"market": "cn", "currency_symbol": "¥", "locale": "zh-CN",
     "lot_size": 100, "t_plus": 1, "up_is_red": true,
     "seed_cash": 100000.0, "midday_break": true,
     "stamp_tax_bps_sell": 5.0, "min_commission": 5.0,
     "default_commission_bps": 2.5,
     "names": {"600519": "贵州茅台", ...},
     "price_limit_pct": {"600519": 10.0, "300750": 20.0, ...}}

  The us payload has names={}, price_limit_pct={} (no daily limits),
  up_is_red=false, lot_size=1, t_plus=0, and seed_cash=10000.0. In CN-1
  every mechanics field is data — nothing enforces it yet (CN-2).

Routes are created via the factory ``create_profile_router`` closing over
the startup-resolved ``MarketProfile``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.market.profiles import MarketProfile


def create_profile_router(profile: MarketProfile) -> APIRouter:
    """Factory: build the market-profile APIRouter for the active profile."""
    router = APIRouter(prefix="/api/market", tags=["market"])

    # Static for the life of the process — build the payload once. The
    # price-limit map covers every seeded ticker; markets without limits
    # (us: price_limit_pct() is always None) serve an empty map.
    limits = {
        ticker: limit
        for ticker in profile.universe.seed_prices
        if (limit := profile.price_limit_pct(ticker)) is not None
    }
    payload = {
        "market": profile.key,
        "currency_symbol": profile.currency_symbol,
        "locale": profile.locale,
        "lot_size": profile.lot_size,
        "t_plus": profile.t_plus,
        "stamp_tax_bps_sell": profile.stamp_tax_bps_sell,
        "min_commission": profile.min_commission,
        "default_commission_bps": profile.default_commission_bps,
        "midday_break": profile.midday_break,
        "up_is_red": profile.up_is_red,
        "seed_cash": profile.seed_cash,
        "names": dict(profile.universe.names),
        "price_limit_pct": limits,
    }

    @router.get("/profile")
    async def get_market_profile() -> dict:
        """The active market's runtime configuration (fixed at startup)."""
        return payload

    return router
