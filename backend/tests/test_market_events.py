"""Tests for GET /api/market/events (market-event news feed).

Covers the empty state, response shape and newest-first ordering, the default
limit, limit clamping, and non-integer limit validation. Events are produced
by driving qualifying (>=1%) tick moves through the shared PriceCache funnel,
exactly as a live market source would.
"""

from __future__ import annotations

import pytest

EVENT_KEYS = {
    "id", "ticker", "headline", "change_percent", "direction", "timestamp",
    "narrative",
}


def _fire_event(fake_market_source, ticker: str, ts: float, up: bool = True) -> None:
    """Drive one qualifying tick move for a fresh ticker (two cache writes)."""
    cache = fake_market_source.price_cache
    cache.update(ticker, 100.00, timestamp=ts - 1.0)
    cache.update(ticker, 103.00 if up else 97.00, timestamp=ts)  # +/-3.0%


@pytest.mark.asyncio
class TestMarketEventsEndpoint:
    """GET /api/market/events response semantics."""

    async def test_empty_state_returns_empty_list(self, app_client):
        """Seeded first ticks are flat — no events yet, still 200 with []."""
        response = await app_client.get("/api/market/events")
        assert response.status_code == 200
        assert response.json() == {"events": []}

    async def test_populated_newest_first_with_contract_shape(
        self, app_client, fake_market_source
    ):
        _fire_event(fake_market_source, "ZAA", ts=1_700_000_010, up=True)
        _fire_event(fake_market_source, "ZBB", ts=1_700_000_020, up=False)

        response = await app_client.get("/api/market/events")
        assert response.status_code == 200
        events = response.json()["events"]
        assert len(events) == 2

        # Newest first
        assert [e["ticker"] for e in events] == ["ZBB", "ZAA"]
        assert events[0]["timestamp"] > events[1]["timestamp"]

        # Exact contract keys and field semantics
        for event in events:
            assert set(event.keys()) == EVENT_KEYS
        assert events[0]["headline"] == "ZBB plunges -3.0% in sudden move"
        assert events[0]["direction"] == "down"
        assert events[0]["change_percent"] == -3.0
        assert events[1]["headline"] == "ZAA surges +3.0% in sudden move"
        assert events[1]["direction"] == "up"
        assert events[1]["change_percent"] == 3.0

    async def test_default_limit_is_20(self, app_client, fake_market_source):
        for i in range(25):
            _fire_event(fake_market_source, f"Z{i:02d}", ts=1_700_000_000 + i * 10)

        response = await app_client.get("/api/market/events")
        assert response.status_code == 200
        assert len(response.json()["events"]) == 20

    async def test_limit_returns_newest_n(self, app_client, fake_market_source):
        _fire_event(fake_market_source, "ZAA", ts=1_700_000_010)
        _fire_event(fake_market_source, "ZBB", ts=1_700_000_020)
        _fire_event(fake_market_source, "ZCC", ts=1_700_000_030)

        response = await app_client.get("/api/market/events?limit=2")
        events = response.json()["events"]
        assert [e["ticker"] for e in events] == ["ZCC", "ZBB"]

    async def test_limit_clamped(self, app_client, fake_market_source):
        _fire_event(fake_market_source, "ZAA", ts=1_700_000_010)
        _fire_event(fake_market_source, "ZBB", ts=1_700_000_020)

        # Above the cap: clamped to 100, not rejected
        high = await app_client.get("/api/market/events?limit=999999")
        assert high.status_code == 200
        assert len(high.json()["events"]) == 2

        # Below 1: clamped up to 1
        for low in ("0", "-5"):
            response = await app_client.get(f"/api/market/events?limit={low}")
            assert response.status_code == 200
            assert len(response.json()["events"]) == 1

    async def test_non_integer_limit_returns_400(self, app_client):
        for bad in ("abc", "2.5", ""):
            response = await app_client.get(f"/api/market/events?limit={bad}")
            assert response.status_code == 400
            assert "error" in response.json()

    async def test_narrative_null_until_enriched_then_value(
        self, app_client, fake_market_source
    ):
        """Events serve narrative=null pre-enrichment, the text afterwards (M3.2a)."""
        _fire_event(fake_market_source, "ZAA", ts=1_700_000_010)

        response = await app_client.get("/api/market/events")
        event = response.json()["events"][0]
        assert event["narrative"] is None

        cache = fake_market_source.price_cache
        assert cache.set_event_narrative(
            event["id"], "ZAA rallies on simulated buyback chatter"
        )

        response = await app_client.get("/api/market/events")
        enriched = response.json()["events"][0]
        assert enriched["id"] == event["id"]
        assert enriched["narrative"] == "ZAA rallies on simulated buyback chatter"
