"""SSE streaming endpoint integration tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.market.cache import PriceCache
from app.market.stream import create_stream_router


@pytest.mark.asyncio
class TestSSEStream:
    """Integration tests for the SSE price streaming endpoint."""

    async def test_stream_returns_event_stream_content_type(self):
        """SSE endpoint responds with text/event-stream content type."""
        cache = PriceCache()
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", "/api/stream/prices") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]

    async def test_stream_sends_retry_directive(self):
        """First event from SSE stream is the retry directive."""
        cache = PriceCache()
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        first_nonempty_line: str | None = None

        async def collect() -> None:
            nonlocal first_nonempty_line
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream("GET", "/api/stream/prices") as response:
                    async for line in response.aiter_lines():
                        if line:
                            first_nonempty_line = line
                            return

        await asyncio.wait_for(collect(), timeout=3.0)
        assert first_nonempty_line is not None
        assert first_nonempty_line.startswith("retry:")

    async def test_stream_sends_price_data_from_cache(self):
        """SSE stream delivers JSON price data for all cached tickers."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        data_line: str | None = None

        async def collect() -> None:
            nonlocal data_line
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream("GET", "/api/stream/prices") as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data_line = line
                            return

        await asyncio.wait_for(collect(), timeout=3.0)

        assert data_line is not None
        payload = json.loads(data_line[len("data: "):])
        assert "AAPL" in payload
        assert "GOOGL" in payload
        assert payload["AAPL"]["ticker"] == "AAPL"
        assert payload["AAPL"]["price"] == 190.00
        assert payload["GOOGL"]["ticker"] == "GOOGL"
        assert payload["GOOGL"]["price"] == 175.00

    async def test_stream_no_data_event_when_cache_empty(self):
        """SSE stream sends no data event when cache is empty (only retry directive)."""
        cache = PriceCache()
        app = FastAPI()
        app.include_router(create_stream_router(cache))

        lines_seen: list[str] = []

        async def collect() -> None:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream("GET", "/api/stream/prices") as response:
                    async for line in response.aiter_lines():
                        lines_seen.append(line)
                        if len(lines_seen) >= 3:
                            return

        await asyncio.wait_for(collect(), timeout=3.0)
        assert any(l.startswith("retry:") for l in lines_seen)
        assert not any(l.startswith("data:") for l in lines_seen)

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
