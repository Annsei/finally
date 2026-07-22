"""SSE streaming endpoint integration tests."""

from __future__ import annotations

import json

import pytest
from fastapi.responses import StreamingResponse

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class DummyRequest:
    client = None

    async def is_disconnected(self) -> bool:
        return False


class DisconnectAfterOneCheckRequest:
    client = None

    def __init__(self) -> None:
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > 1


def _prices_endpoint(router):
    for route in router.routes:
        if getattr(route, "path", "").endswith("/prices"):
            return route.endpoint
    raise AssertionError("Router must expose /prices endpoint")


@pytest.mark.asyncio
class TestSSEStream:
    """Integration tests for the SSE price streaming endpoint."""

    async def test_stream_returns_event_stream_content_type(self):
        """SSE endpoint responds with text/event-stream content type."""
        cache = PriceCache()
        router = create_stream_router(cache)
        endpoint = _prices_endpoint(router)

        response = await endpoint(DummyRequest())

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"

    async def test_stream_sends_retry_directive(self):
        """First event from SSE stream is the retry directive."""
        cache = PriceCache()

        gen = _generate_events(cache, DummyRequest())
        first_event = await anext(gen)
        await gen.aclose()

        assert first_event.startswith("retry:")

    async def test_stream_sends_price_data_from_cache(self):
        """SSE stream delivers JSON price data for all cached tickers."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)

        gen = _generate_events(cache, DummyRequest())
        await anext(gen)
        data_event = await anext(gen)
        await gen.aclose()

        assert data_event.startswith("data:")
        payload = json.loads(data_event[len("data: "):])
        assert "AAPL" in payload
        assert "GOOGL" in payload
        assert payload["AAPL"]["ticker"] == "AAPL"
        assert payload["AAPL"]["price"] == 190.00
        assert payload["GOOGL"]["ticker"] == "GOOGL"
        assert payload["GOOGL"]["price"] == 175.00

    async def test_stream_no_data_event_when_cache_empty(self):
        """SSE stream sends no data event when cache is empty (only retry directive)."""
        cache = PriceCache()

        gen = _generate_events(cache, DisconnectAfterOneCheckRequest(), interval=0)
        first_event = await anext(gen)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)
        await gen.aclose()

        assert first_event.startswith("retry:")

    async def test_stream_sends_named_heartbeat_when_prices_are_quiet(self):
        cache = PriceCache()
        gen = _generate_events(
            cache,
            DummyRequest(),
            interval=0,
            heartbeat_interval=0,
        )
        await anext(gen)  # retry directive
        heartbeat = await anext(gen)
        await gen.aclose()

        assert heartbeat == "event: heartbeat\ndata: {}\n\n"

    async def test_multiple_router_instances_do_not_conflict(self):
        """Each call to create_stream_router() produces an independent router."""
        cache1 = PriceCache()
        cache1.update("AAPL", 190.00)
        cache2 = PriceCache()
        cache2.update("TSLA", 250.00)

        router1 = create_stream_router(cache1)
        router2 = create_stream_router(cache2)

        # Routers should be distinct objects (not the same module-level singleton)
        assert router1 is not router2
