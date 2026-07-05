"""Regression tests for the code-review bug fixes.

Covers:
- FIX 1: watchlist routes and the chat handler sync add/remove to the live
  market data source (app.state.market_source)
- FIX 2: chat multi-trade execution is atomic — one commit covers all trades,
  watchlist changes, and chat messages; per-trade validation failures stay
  non-fatal; unexpected mid-batch errors roll back everything
- FIX 3: selling the exact fractional quantity owned deletes the position row
  (no float-residue ghost positions)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.connection import get_conn


@pytest.mark.asyncio
class TestWatchlistMarketSourceSync:
    """FIX 1: watchlist DB changes must reach the market data source."""

    async def test_add_ticker_calls_market_source_add(self, app_client, fake_market_source):
        """POST /api/watchlist must call market_source.add_ticker with the new ticker."""
        resp = await app_client.post("/api/watchlist/", json={"ticker": "PYPL"})
        assert resp.status_code == 200
        assert "PYPL" in fake_market_source.added

    async def test_add_ticker_normalized_before_source_call(self, app_client, fake_market_source):
        """Lowercase input is normalized to uppercase before reaching the source."""
        resp = await app_client.post("/api/watchlist/", json={"ticker": "shop"})
        assert resp.status_code == 200
        assert "SHOP" in fake_market_source.added
        assert "shop" not in fake_market_source.added

    async def test_invalid_ticker_does_not_call_source(self, app_client, fake_market_source):
        """A 400-rejected ticker must not be forwarded to the market source."""
        resp = await app_client.post("/api/watchlist/", json={"ticker": "  "})
        assert resp.status_code == 400
        assert fake_market_source.added == []

    async def test_remove_ticker_calls_market_source_remove(self, app_client, fake_market_source):
        """DELETE /api/watchlist/{ticker} must call market_source.remove_ticker."""
        resp = await app_client.delete("/api/watchlist/NFLX")
        assert resp.status_code == 200
        assert "NFLX" in fake_market_source.removed

    async def test_source_failure_keeps_db_change(self, app_client, fake_market_source, monkeypatch):
        """If the market source raises, the DB change stands and the route still returns 200."""

        async def boom(ticker: str) -> None:
            raise RuntimeError("source unavailable")

        monkeypatch.setattr(fake_market_source, "add_ticker", boom)

        resp = await app_client.post("/api/watchlist/", json={"ticker": "PYPL"})
        assert resp.status_code == 200

        get_resp = await app_client.get("/api/watchlist/")
        tickers = [t["ticker"] for t in get_resp.json()["tickers"]]
        assert "PYPL" in tickers

    async def test_chat_watchlist_add_calls_market_source(self, chat_client, fake_market_source):
        """Chat-applied watchlist changes (mock adds PYPL) must reach the market source."""
        resp = await chat_client.post("/api/chat/", json={"message": "add pypl please"})
        assert resp.status_code == 200
        assert "PYPL" in fake_market_source.added


@pytest.mark.asyncio
class TestGhostPositionEpsilon:
    """FIX 3: float residue must not leave ghost positions after a full sell."""

    async def test_sell_exact_fractional_quantity_deletes_position(self, app_client):
        """Buy 0.1 three times (sum = 0.30000000000000004), sell 0.3 — row must be gone."""
        for _ in range(3):
            buy = await app_client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "quantity": 0.1, "side": "buy"},
            )
            assert buy.status_code == 200

        sell = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 0.3, "side": "sell"},
        )
        assert sell.status_code == 200

        portfolio = await app_client.get("/api/portfolio/")
        tickers = [p["ticker"] for p in portfolio.json()["positions"]]
        assert "AAPL" not in tickers, "residual float quantity must not leave a ghost position"

    async def test_sell_full_position_deletes_row_direct(self, tmp_path):
        """Direct helper check: selling the exact owned fractional quantity deletes the row."""
        from app.db.connection import init_db
        from app.market import PriceCache
        from app.market.seed_prices import SEED_PRICES
        from app.routes.portfolio import execute_trade_on_conn

        db_file = str(tmp_path / "ghost.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            cache = PriceCache()
            for ticker, price in SEED_PRICES.items():
                cache.update(ticker, price)

            for _ in range(3):
                assert execute_trade_on_conn(conn, cache, "MSFT", "buy", 0.1)["status"] == "executed"
            assert execute_trade_on_conn(conn, cache, "MSFT", "sell", 0.3)["status"] == "executed"
            conn.commit()

            row = conn.execute(
                "SELECT quantity FROM positions WHERE user_id = 'default' AND ticker = 'MSFT'"
            ).fetchone()
            assert row is None, f"expected position deleted, found residual qty {row['quantity'] if row else None}"
        finally:
            conn.close()


def _fake_completion_factory(payload: dict):
    """Build a litellm.completion stand-in returning the given structured payload."""

    def fake_completion(*args, **kwargs):
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


@pytest.mark.asyncio
class TestChatTransactionBoundary:
    """FIX 2: one atomic commit for the whole chat turn."""

    async def test_partial_trade_failure_keeps_messages_and_first_trade(
        self, chat_client, monkeypatch
    ):
        """Two LLM trades, second fails validation: 200, first trade committed, messages recorded."""
        import litellm

        payload = {
            "message": "Bought AAPL; could not sell MSFT.",
            "trades": [
                {"ticker": "AAPL", "side": "buy", "quantity": 1},
                {"ticker": "MSFT", "side": "sell", "quantity": 5},  # no MSFT held -> fails
            ],
            "watchlist_changes": [],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post("/api/chat/", json={"message": "buy aapl, sell msft"})
        assert resp.status_code == 200
        data = resp.json()

        # Per-trade validation failure is non-fatal and reported in outcomes
        assert data["trades"][0]["status"] == "executed"
        assert data["trades"][1]["status"] == "failed"
        assert "Insufficient shares" in data["trades"][1]["error"]

        # Both chat messages are committed (visible on a fresh connection)
        history = await chat_client.get("/api/chat/")
        messages = history.json()["messages"]
        assert len(messages) == 2
        assert [m["role"] for m in messages] == ["user", "assistant"]

        # The successful first trade is committed atomically with the messages
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        tickers = [p["ticker"] for p in portfolio["positions"]]
        assert "AAPL" in tickers
        assert portfolio["cash"] < 10000.0

    async def test_unexpected_error_mid_batch_rolls_back_everything(
        self, chat_client, monkeypatch
    ):
        """An unexpected exception on trade 2 must roll back trade 1 AND the chat turn."""
        import litellm

        import app.routes.chat as chat_module

        payload = {
            "message": "Executing two trades.",
            "trades": [
                {"ticker": "AAPL", "side": "buy", "quantity": 1},
                {"ticker": "GOOGL", "side": "buy", "quantity": 1},
            ],
            "watchlist_changes": [],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        real_execute = chat_module.execute_trade_on_conn
        calls = {"count": 0}

        def exploding_execute(conn, price_cache, ticker, side, quantity):
            calls["count"] += 1
            if calls["count"] >= 2:
                raise RuntimeError("boom mid-batch")
            return real_execute(conn, price_cache, ticker, side, quantity)

        monkeypatch.setattr(chat_module, "execute_trade_on_conn", exploding_execute)

        with pytest.raises(RuntimeError, match="boom mid-batch"):
            await chat_client.post("/api/chat/", json={"message": "buy two stocks"})

        # Nothing committed: no trades, full cash, no chat messages
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        assert portfolio["positions"] == []
        assert portfolio["cash"] == 10000.0

        history = await chat_client.get("/api/chat/")
        assert history.json()["messages"] == []

    async def test_trade_endpoint_commits_trade_and_snapshot(self, app_client):
        """POST /api/portfolio/trade still persists the trade and its snapshot."""
        resp = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy"},
        )
        assert resp.status_code == 200

        history = await app_client.get("/api/portfolio/history")
        assert len(history.json()["snapshots"]) >= 1

    async def test_chat_trade_records_snapshot(self, chat_client):
        """Mock chat turn (buys 5 AAPL) records a portfolio snapshot after committing."""
        resp = await chat_client.post("/api/chat/", json={"message": "buy aapl"})
        assert resp.status_code == 200

        history = await chat_client.get("/api/portfolio/history")
        assert len(history.json()["snapshots"]) >= 1


@pytest.mark.asyncio
class TestChatAddThenTradeOrdering:
    """FIX 4: watchlist adds must reach the market source (seeding the price
    cache) BEFORE LLM-requested trades execute, so a single chat turn like
    "add PYPL to my watchlist and buy 5 shares" succeeds."""

    async def test_add_and_buy_same_new_ticker_in_one_turn(
        self, chat_client, fake_market_source, monkeypatch
    ):
        """Watchlist add of a brand-new ticker + trade on it in ONE turn: trade executes."""
        import litellm

        payload = {
            "message": "Added PYPL to your watchlist and bought 5 shares.",
            "trades": [{"ticker": "PYPL", "side": "buy", "quantity": 5}],
            "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post(
            "/api/chat/", json={"message": "Add PYPL to my watchlist and buy 5 shares"}
        )
        assert resp.status_code == 200
        data = resp.json()

        # The watchlist add was applied and synced to the market source ...
        assert data["watchlist_changes"][0]["status"] == "added"
        assert data["watchlist_changes"][0]["ticker"] == "PYPL"
        assert "PYPL" in fake_market_source.added

        # ... BEFORE the trade ran, so the trade found a price and executed
        # (the pre-fix behavior was status=failed / "Ticker not found in price cache").
        trade = data["trades"][0]
        assert trade["status"] == "executed", trade
        assert trade["ticker"] == "PYPL"
        assert trade["price"] > 0

        # Cash decreased by exactly quantity * fill price and the position row exists
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == pytest.approx(10000.0 - 5 * trade["price"])
        assert "PYPL" in [p["ticker"] for p in portfolio["positions"]]

        # And PYPL is persisted in the watchlist
        watchlist = (await chat_client.get("/api/watchlist/")).json()
        assert "PYPL" in [t["ticker"] for t in watchlist["tickers"]]
