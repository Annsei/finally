"""Tests for POST /api/chat handler internals.

These tests exercise the handler's mock path directly (DB setup + calling the
inner coroutine), bypassing the ASGI layer which tests/test_chat.py covers:
- Handler persists two chat_messages rows per request
- Mock response structure and auto-execution (trades, watchlist changes)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _post_chat_handler(router):
    for route in router.routes:
        if hasattr(route, "endpoint") and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("Router must expose a POST route endpoint")


def _stub_request():
    """Minimal Request stand-in exposing .app.state for direct handler calls.

    The handler looks up app.state.market_source (absent here, so the
    market-source sync is skipped — matching apps without a live source).
    """
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


class TestHandlerMockPathIntegration:
    """Handler mock path persists messages and returns correct structure.

    These tests exercise the handler directly by setting up a DB and calling
    the inner coroutine, bypassing the ASGI layer (which 02-03 will cover).
    """

    @pytest.fixture
    def db_and_cache(self, tmp_path, monkeypatch):
        """Provide an initialized DB + seeded price cache."""
        db_path = str(tmp_path / "chat_test.db")
        monkeypatch.setenv("DB_PATH", db_path)
        monkeypatch.setenv("LLM_MOCK", "true")

        from app.db.connection import init_db
        from app.market import PriceCache
        from app.market.seed_prices import SEED_PRICES

        init_db(db_path)

        cache = PriceCache()
        for ticker, price in SEED_PRICES.items():
            cache.update(ticker, price)

        return db_path, cache

    @pytest.mark.asyncio
    async def test_mock_response_message_exact(self, db_and_cache):
        """Mock path returns exact D-06 message string."""
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        result = await handler(body=ChatRequest(message="hello"), request=_stub_request())
        assert result["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )

    @pytest.mark.asyncio
    async def test_mock_response_has_trades_key(self, db_and_cache):
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        result = await handler(body=ChatRequest(message="hello"), request=_stub_request())
        assert "trades" in result

    @pytest.mark.asyncio
    async def test_mock_response_has_watchlist_changes_key(self, db_and_cache):
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        result = await handler(body=ChatRequest(message="hello"), request=_stub_request())
        assert "watchlist_changes" in result

    @pytest.mark.asyncio
    async def test_mock_aapl_trade_executes(self, db_and_cache):
        """With AAPL price seeded, the AAPL buy trade executes successfully."""
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        result = await handler(body=ChatRequest(message="hello"), request=_stub_request())
        assert len(result["trades"]) == 1
        trade = result["trades"][0]
        assert trade["status"] == "executed"
        assert trade["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_mock_pypl_watchlist_add_executes(self, db_and_cache):
        """PYPL is added to the watchlist table after the mock request."""
        db_path, cache = db_and_cache
        from app.db.connection import get_conn
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        await handler(body=ChatRequest(message="hello"), request=_stub_request())

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = 'default' AND ticker = 'PYPL'"
            ).fetchone()
            assert row is not None, "PYPL should be in watchlist after mock request"
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_two_chat_messages_persisted(self, db_and_cache):
        """After one request, two rows exist in chat_messages (user + assistant)."""
        db_path, cache = db_and_cache
        from app.db.connection import get_conn
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        await handler(body=ChatRequest(message="hello"), request=_stub_request())

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE user_id = 'default'"
            ).fetchone()
            assert row["cnt"] == 2
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_assistant_row_has_actions_json(self, db_and_cache):
        """Assistant chat_messages row has non-null actions column with valid JSON."""
        import json as json_lib

        db_path, cache = db_and_cache
        from app.db.connection import get_conn
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        await handler(body=ChatRequest(message="hello"), request=_stub_request())

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT actions FROM chat_messages WHERE user_id = 'default' AND role = 'assistant'"
            ).fetchone()
            assert row is not None
            assert row["actions"] is not None
            actions = json_lib.loads(row["actions"])
            assert "trades" in actions
            assert "watchlist_changes" in actions
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_second_request_adds_two_more_rows(self, db_and_cache):
        """Each request appends 2 rows — history loaded but not re-inserted."""
        db_path, cache = db_and_cache
        from app.db.connection import get_conn
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = _post_chat_handler(router)

        # First request
        await handler(body=ChatRequest(message="first message"), request=_stub_request())
        # Second request
        await handler(body=ChatRequest(message="second message"), request=_stub_request())

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE user_id = 'default'"
            ).fetchone()
            assert row["cnt"] == 4
        finally:
            conn.close()
