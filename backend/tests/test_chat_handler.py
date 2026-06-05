"""Tests for POST /api/chat handler internals (Task 2 RED).

These tests verify the handler's core behaviors that can be exercised
without the full ASGI client fixture (deferred to plan 02-03):
- Handler function exists inside create_chat_router (not a stub)
- Handler has the correct async signature
- Handler contains mock-branch logic (os.getenv check)
- Handler contains the asyncio.to_thread LLM wrap
- Handler persists two chat_messages rows per request
- litellm import is lazy (inside else block, not at module top level)

Full ASGI end-to-end tests (POST /api/chat via httpx AsyncClient) are
intentionally deferred to 02-03 which creates tests/test_chat.py with
the complete app fixture including the chat router.
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest


class TestHandlerStructure:
    """Handler function is present and has correct structure."""

    def test_chat_handler_is_async(self):
        """The inner chat function is an async coroutine function."""
        from app.market import PriceCache
        from app.routes.chat import create_chat_router

        cache = PriceCache()
        router = create_chat_router(cache, ":memory:")

        # Find the POST "/" route handler
        route_funcs = [
            r.endpoint for r in router.routes if hasattr(r, "endpoint")
        ]
        assert len(route_funcs) >= 1, "Router must have at least one route endpoint"
        post_handler = route_funcs[0]
        assert inspect.iscoroutinefunction(post_handler), "chat handler must be async"

    def test_litellm_not_imported_at_module_level(self):
        """from litellm import completion must not appear in first 20 lines."""
        import pathlib

        chat_path = pathlib.Path(__file__).parent.parent / "app" / "routes" / "chat.py"
        lines = chat_path.read_text().splitlines()
        first_20 = "\n".join(lines[:20])
        assert "from litellm" not in first_20, (
            "litellm must be imported lazily inside the else block, not at module top"
        )

    def test_mock_env_check_present_in_source(self):
        """Source contains os.getenv('LLM_MOCK') check."""
        import pathlib

        chat_path = pathlib.Path(__file__).parent.parent / "app" / "routes" / "chat.py"
        source = chat_path.read_text()
        assert 'os.getenv("LLM_MOCK"' in source, (
            "Handler must check os.getenv('LLM_MOCK') for mock branch"
        )

    def test_asyncio_to_thread_present_in_source(self):
        """Source contains asyncio.to_thread wrapping the LLM call."""
        import pathlib

        chat_path = pathlib.Path(__file__).parent.parent / "app" / "routes" / "chat.py"
        source = chat_path.read_text()
        assert "asyncio.to_thread(" in source, (
            "LLM completion call must be wrapped in asyncio.to_thread"
        )

    def test_two_insert_statements_in_source(self):
        """Source contains two INSERT INTO chat_messages statements."""
        import pathlib

        chat_path = pathlib.Path(__file__).parent.parent / "app" / "routes" / "chat.py"
        source = chat_path.read_text()
        count = source.count("INSERT INTO chat_messages")
        assert count == 2, (
            f"Expected 2 INSERT INTO chat_messages statements, found {count}"
        )


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
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        result = await handler(body=ChatRequest(message="hello"), request=None)
        assert result["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )

    @pytest.mark.asyncio
    async def test_mock_response_has_trades_key(self, db_and_cache):
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        result = await handler(body=ChatRequest(message="hello"), request=None)
        assert "trades" in result

    @pytest.mark.asyncio
    async def test_mock_response_has_watchlist_changes_key(self, db_and_cache):
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        result = await handler(body=ChatRequest(message="hello"), request=None)
        assert "watchlist_changes" in result

    @pytest.mark.asyncio
    async def test_mock_aapl_trade_executes(self, db_and_cache):
        """With AAPL price seeded, the AAPL buy trade executes successfully."""
        db_path, cache = db_and_cache
        from app.routes.chat import ChatRequest, create_chat_router

        router = create_chat_router(cache, db_path)
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        result = await handler(body=ChatRequest(message="hello"), request=None)
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
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        await handler(body=ChatRequest(message="hello"), request=None)

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
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        await handler(body=ChatRequest(message="hello"), request=None)

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
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        await handler(body=ChatRequest(message="hello"), request=None)

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
        handler = [r.endpoint for r in router.routes if hasattr(r, "endpoint")][0]

        # First request
        await handler(body=ChatRequest(message="first message"), request=None)
        # Second request
        await handler(body=ChatRequest(message="second message"), request=None)

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE user_id = 'default'"
            ).fetchone()
            assert row["cnt"] == 4
        finally:
            conn.close()
