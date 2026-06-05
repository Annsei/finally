"""Tests for chat.py Pydantic models, constants, and context helper (Task 1 RED).

These tests verify:
- Module importability
- Pydantic model schemas
- Module-level constants
- _assemble_portfolio_context helper behavior
- create_chat_router returns an APIRouter
"""

from __future__ import annotations

import sqlite3

import pytest


class TestChatModuleImport:
    """chat.py is importable with no errors."""

    def test_import_chat_response(self):
        from app.routes.chat import ChatResponse  # noqa: F401

    def test_import_chat_request(self):
        from app.routes.chat import ChatRequest  # noqa: F401

    def test_import_trade_instruction(self):
        from app.routes.chat import TradeInstruction  # noqa: F401

    def test_import_watchlist_change(self):
        from app.routes.chat import WatchlistChange  # noqa: F401

    def test_import_assemble_portfolio_context(self):
        from app.routes.chat import _assemble_portfolio_context  # noqa: F401

    def test_import_create_chat_router(self):
        from app.routes.chat import create_chat_router  # noqa: F401


class TestChatPydanticModels:
    """Pydantic model field contracts."""

    def test_chat_request_has_message_field(self):
        from app.routes.chat import ChatRequest

        req = ChatRequest(message="hello")
        assert req.message == "hello"

    def test_trade_instruction_fields(self):
        from app.routes.chat import TradeInstruction

        t = TradeInstruction(ticker="AAPL", side="buy", quantity=5.0)
        assert t.ticker == "AAPL"
        assert t.side == "buy"
        assert t.quantity == 5.0

    def test_watchlist_change_fields(self):
        from app.routes.chat import WatchlistChange

        w = WatchlistChange(ticker="PYPL", action="add")
        assert w.ticker == "PYPL"
        assert w.action == "add"

    def test_chat_response_message_field(self):
        from app.routes.chat import ChatResponse

        r = ChatResponse(message="hi")
        assert r.message == "hi"

    def test_chat_response_trades_default_empty_list(self):
        from app.routes.chat import ChatResponse

        r = ChatResponse(message="hi")
        assert r.trades == []

    def test_chat_response_watchlist_changes_default_empty_list(self):
        from app.routes.chat import ChatResponse

        r = ChatResponse(message="hi")
        assert r.watchlist_changes == []

    def test_chat_response_accepts_trades(self):
        from app.routes.chat import ChatResponse, TradeInstruction

        r = ChatResponse(
            message="ok",
            trades=[TradeInstruction(ticker="AAPL", side="buy", quantity=10)],
        )
        assert len(r.trades) == 1
        assert r.trades[0].ticker == "AAPL"

    def test_chat_response_accepts_watchlist_changes(self):
        from app.routes.chat import ChatResponse, WatchlistChange

        r = ChatResponse(
            message="ok",
            watchlist_changes=[WatchlistChange(ticker="PYPL", action="add")],
        )
        assert len(r.watchlist_changes) == 1
        assert r.watchlist_changes[0].ticker == "PYPL"


class TestChatModuleConstants:
    """MODULE contains the correct LLM constants."""

    def test_model_constant(self):
        import app.routes.chat as chat_module

        assert chat_module.MODEL == "openrouter/openai/gpt-oss-120b"

    def test_extra_body_constant(self):
        import app.routes.chat as chat_module

        assert chat_module.EXTRA_BODY == {"provider": {"order": ["cerebras"]}}


class TestAssemblePortfolioContext:
    """_assemble_portfolio_context returns correctly formatted string."""

    def _make_empty_db(self, tmp_path) -> tuple[sqlite3.Connection, str]:
        """Create an initialized DB file and return (conn, db_path)."""
        db_path = str(tmp_path / "test_context.db")
        from app.db.connection import init_db

        init_db(db_path)

        from app.db.connection import get_conn

        conn = get_conn(db_path)
        return conn, db_path

    def test_context_starts_with_cash(self, tmp_path):
        from app.market import PriceCache
        from app.routes.chat import _assemble_portfolio_context

        conn, _ = self._make_empty_db(tmp_path)
        try:
            cache = PriceCache()
            result = _assemble_portfolio_context(conn, cache)
            assert result.startswith("Cash: $")
        finally:
            conn.close()

    def test_context_contains_total_portfolio_value(self, tmp_path):
        from app.market import PriceCache
        from app.routes.chat import _assemble_portfolio_context

        conn, _ = self._make_empty_db(tmp_path)
        try:
            cache = PriceCache()
            result = _assemble_portfolio_context(conn, cache)
            assert "Total portfolio value: $" in result
        finally:
            conn.close()

    def test_context_no_positions_shows_placeholder(self, tmp_path):
        from app.market import PriceCache
        from app.routes.chat import _assemble_portfolio_context

        conn, _ = self._make_empty_db(tmp_path)
        try:
            cache = PriceCache()
            result = _assemble_portfolio_context(conn, cache)
            assert "(no open positions)" in result
        finally:
            conn.close()

    def test_context_watchlist_line_format(self, tmp_path):
        from app.market import PriceCache
        from app.routes.chat import _assemble_portfolio_context

        conn, _ = self._make_empty_db(tmp_path)
        try:
            cache = PriceCache()
            result = _assemble_portfolio_context(conn, cache)
            # Should contain "Watchlist: " with comma-separated tickers
            assert "Watchlist: " in result
        finally:
            conn.close()

    def test_context_with_position_shows_ticker(self, tmp_path):
        """When a position exists, the context includes it."""
        import uuid
        from datetime import datetime, timezone

        from app.market import PriceCache
        from app.market.seed_prices import SEED_PRICES
        from app.routes.chat import _assemble_portfolio_context

        conn, _ = self._make_empty_db(tmp_path)
        try:
            # Insert an AAPL position
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                "VALUES (?, 'default', 'AAPL', 10, 180.0, ?)",
                (str(uuid.uuid4()), now),
            )
            conn.commit()

            cache = PriceCache()
            cache.update("AAPL", SEED_PRICES["AAPL"])
            result = _assemble_portfolio_context(conn, cache)
            assert "AAPL" in result
            assert "(no open positions)" not in result
        finally:
            conn.close()


class TestCreateChatRouter:
    """create_chat_router returns a proper APIRouter."""

    def test_returns_apirouter(self, tmp_path):
        from fastapi import APIRouter

        from app.market import PriceCache
        from app.routes.chat import create_chat_router

        cache = PriceCache()
        router = create_chat_router(cache, str(tmp_path / "test.db"))
        assert isinstance(router, APIRouter)

    def test_router_is_not_none(self, tmp_path):
        from app.market import PriceCache
        from app.routes.chat import create_chat_router

        cache = PriceCache()
        router = create_chat_router(cache, str(tmp_path / "test.db"))
        assert router is not None
