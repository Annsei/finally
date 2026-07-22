"""GET /api/market/profile endpoint (CN-1) — us and cn payload shapes.

The response is the frontend's runtime market contract
(planning/CN1_PROFILE_CONTRACT.md §5) — field names are load-bearing.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market.profiles import CN_PROFILE, US_PROFILE, MarketProfile
from app.market.seed_prices_cn import CN_SEED_PRICES
from app.routes.profile import create_profile_router

EXPECTED_FIELDS = {
    "market",
    "currency_symbol",
    "locale",
    "lot_size",
    "t_plus",
    "stamp_tax_bps_sell",
    "min_commission",
    "default_commission_bps",
    "midday_break",
    "up_is_red",
    "seed_cash",
    "names",
    "price_limit_pct",
}


async def _get_profile_payload(profile: MarketProfile) -> dict:
    app = FastAPI()
    app.include_router(create_profile_router(profile))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/market/profile")
    assert response.status_code == 200
    return response.json()


class TestUSProfileEndpoint:
    async def test_us_payload(self):
        data = await _get_profile_payload(US_PROFILE)
        assert set(data) == EXPECTED_FIELDS
        assert data["market"] == "us"
        assert data["currency_symbol"] == "$"
        assert data["locale"] == "en-US"
        assert data["lot_size"] == 1
        assert data["t_plus"] == 0
        assert data["up_is_red"] is False
        assert data["midday_break"] is False
        assert data["seed_cash"] == 10_000.0

    async def test_us_maps_are_empty(self):
        data = await _get_profile_payload(US_PROFILE)
        assert data["names"] == {}
        assert data["price_limit_pct"] == {}


class TestCNProfileEndpoint:
    async def test_cn_payload(self):
        data = await _get_profile_payload(CN_PROFILE)
        assert set(data) == EXPECTED_FIELDS
        assert data["market"] == "cn"
        assert data["currency_symbol"] == "¥"
        assert data["locale"] == "zh-CN"
        assert data["lot_size"] == 100
        assert data["t_plus"] == 1
        assert data["up_is_red"] is True
        assert data["midday_break"] is True
        assert data["seed_cash"] == 100_000.0
        assert data["stamp_tax_bps_sell"] == 5.0
        assert data["min_commission"] == 5.0
        assert data["default_commission_bps"] == 2.5

    async def test_cn_names_map(self):
        data = await _get_profile_payload(CN_PROFILE)
        assert data["names"]["600519"] == "贵州茅台"
        assert set(data["names"]) == set(CN_SEED_PRICES)

    async def test_cn_price_limit_map_covers_all_tickers(self):
        data = await _get_profile_payload(CN_PROFILE)
        limits = data["price_limit_pct"]
        assert set(limits) == set(CN_SEED_PRICES)
        assert limits["600519"] == 10.0
        assert limits["300750"] == 20.0
        assert limits["688981"] == 20.0
        assert limits["300059"] == 20.0
