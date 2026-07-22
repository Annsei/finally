"""Chat agent tests (M2.1 + M2.2): LLM-driven advanced orders and standing rules.

Extends the litellm.completion monkeypatch pattern from
test_bugfix_regressions.py to drive the chat pipeline with structured
responses containing ``orders`` and ``rules`` arrays, and verifies:
- resting / immediately-marketable / invalid orders produce the right rows,
  statuses, and actions.orders shapes; per-order failures are non-fatal
- rules arrays create rule rows with actions.rules outcomes
- the LLM_MOCK deterministic payload stays byte-identical (no orders/rules keys)
- an unexpected mid-batch error still rolls back the entire chat turn
- the system prompt documents the new capabilities
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.market.seed_prices import SEED_PRICES
from tests.test_orders import ORDER_JSON_KEYS
from tests.test_rules import RULE_JSON_KEYS

AAPL_PRICE = SEED_PRICES["AAPL"]  # 190.0


def _fake_completion_factory(payload: dict):
    """Build a litellm.completion stand-in returning the given structured payload."""

    def fake_completion(*args, **kwargs):
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


def _capturing_completion_factory(captured: dict):
    """litellm.completion stand-in that records kwargs and returns valid JSON."""

    def fake_completion(*args, **kwargs):
        captured.update(kwargs)
        content = json.dumps({"message": "ok", "trades": [], "watchlist_changes": []})
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


@pytest.mark.asyncio
class TestChatPlacesOrders:
    """M2.1: the ``orders`` array in the LLM response places advanced orders."""

    async def test_mixed_order_batch_outcomes_and_rows(self, chat_client, monkeypatch):
        """Resting limit + immediately-marketable limit + invalid wrong-side stop."""
        import litellm

        payload = {
            "message": "Placed your orders.",
            "trades": [],
            "watchlist_changes": [],
            "orders": [
                # 1) resting: buy limit far below the $190 market
                {"ticker": "AAPL", "side": "buy", "quantity": 2, "kind": "limit",
                 "limit_price": 150.0},
                # 2) immediately marketable: buy limit above the market -> fills now
                {"ticker": "AAPL", "side": "buy", "quantity": 1, "kind": "limit",
                 "limit_price": 250.0, "time_in_force": "day"},
                # 3) invalid: SELL stop above the market (wrong side) -> failed
                {"ticker": "AAPL", "side": "sell", "quantity": 1, "kind": "stop",
                 "stop_price": 250.0},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post("/api/chat/", json={"message": "set up my orders"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Placed your orders."
        outcomes = data["orders"]
        assert len(outcomes) == 3

        # [0] resting order: full order JSON, status 'open'
        assert set(outcomes[0].keys()) == ORDER_JSON_KEYS
        assert outcomes[0]["status"] == "open"
        assert outcomes[0]["kind"] == "limit"
        assert outcomes[0]["limit_price"] == 150.0
        assert outcomes[0]["time_in_force"] == "gtc"

        # [1] marketable order: full order JSON, status 'filled' at the ask
        assert set(outcomes[1].keys()) == ORDER_JSON_KEYS
        assert outcomes[1]["status"] == "filled"
        assert outcomes[1]["fill_price"] == AAPL_PRICE
        assert outcomes[1]["time_in_force"] == "day"
        assert outcomes[1]["expires_at"] is not None

        # [2] failed order: failure dict, no order row — and non-fatal
        assert outcomes[2] == {
            "status": "failed",
            "ticker": "AAPL",
            "error": "Stop price must be below the market",
        }

        # Order rows committed with the right statuses (newest first)
        orders = (await chat_client.get("/api/portfolio/orders")).json()["orders"]
        assert len(orders) == 2
        assert {o["status"] for o in orders} == {"open", "filled"}
        assert {o["id"] for o in orders} == {outcomes[0]["id"], outcomes[1]["id"]}

        # The immediate fill moved money and created the position + snapshot
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == pytest.approx(10000.0 - AAPL_PRICE)
        assert [p["ticker"] for p in portfolio["positions"]] == ["AAPL"]
        history = (await chat_client.get("/api/portfolio/history")).json()["snapshots"]
        assert len(history) >= 1

        # Stored assistant actions carry the same outcomes under "orders"
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        actions = messages[-1]["actions"]
        assert messages[-1]["role"] == "assistant"
        assert actions["orders"] == outcomes
        assert actions["trades"] == [] and actions["watchlist_changes"] == []

    async def test_orders_after_trades_share_one_transaction(self, chat_client, monkeypatch):
        """Trades run first, orders after — a sell stop for shares bought in the
        SAME turn validates against the post-trade position/market state."""
        import litellm

        payload = {
            "message": "Bought AAPL and protected it with a stop.",
            "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5}],
            "watchlist_changes": [],
            "orders": [
                {"ticker": "AAPL", "side": "sell", "quantity": 5, "kind": "stop",
                 "stop_price": 150.0},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post(
            "/api/chat/", json={"message": "buy 5 AAPL, stop at 150"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trades"][0]["status"] == "executed"
        assert data["orders"][0]["status"] == "open"
        assert data["orders"][0]["kind"] == "stop"
        assert data["orders"][0]["stop_price"] == 150.0

        orders = (await chat_client.get("/api/portfolio/orders?status=open")).json()["orders"]
        assert len(orders) == 1

    async def test_unexpected_error_mid_order_batch_rolls_back_everything(
        self, chat_client, monkeypatch
    ):
        """Extends the FIX-2 regression pattern: an unexpected exception while
        placing order 2 must roll back the trade, order 1 (including its
        immediate fill), and both chat messages."""
        import litellm

        import app.routes.chat as chat_module

        payload = {
            "message": "Trading and placing two orders.",
            "trades": [{"ticker": "MSFT", "side": "buy", "quantity": 1}],
            "watchlist_changes": [],
            "orders": [
                # marketable -> fills immediately (must also be rolled back)
                {"ticker": "AAPL", "side": "buy", "quantity": 1, "kind": "limit",
                 "limit_price": 250.0},
                {"ticker": "AAPL", "side": "buy", "quantity": 1, "kind": "limit",
                 "limit_price": 150.0},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        real_place = chat_module.place_order_on_conn
        calls = {"count": 0}

        def exploding_place(conn, price_cache, **kwargs):
            calls["count"] += 1
            if calls["count"] >= 2:
                raise RuntimeError("boom mid-order-batch")
            return real_place(conn, price_cache, **kwargs)

        monkeypatch.setattr(chat_module, "place_order_on_conn", exploding_place)

        with pytest.raises(RuntimeError, match="boom mid-order-batch"):
            await chat_client.post("/api/chat/", json={"message": "do it all"})

        # Nothing committed: no orders, no trades, full cash, no chat messages
        orders = (await chat_client.get("/api/portfolio/orders")).json()["orders"]
        assert orders == []
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        assert portfolio["positions"] == []
        assert portfolio["cash"] == 10000.0
        history = (await chat_client.get("/api/chat/")).json()
        assert history["messages"] == []


@pytest.mark.asyncio
class TestChatCreatesRules:
    """M2.2: the ``rules`` array in the LLM response creates standing rules."""

    async def test_rules_created_with_outcomes(self, chat_client, monkeypatch):
        import litellm

        payload = {
            "message": "Rule set: I'll buy the NVDA dip.",
            "trades": [],
            "watchlist_changes": [],
            "rules": [
                {"ticker": "NVDA", "trigger_type": "day_change_pct_below",
                 "threshold": -3, "side": "buy", "quantity": 5},
                # unknown ticker -> per-rule failure, non-fatal
                {"ticker": "ZZZZ", "trigger_type": "price_above",
                 "threshold": 100, "side": "buy", "quantity": 1},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post(
            "/api/chat/", json={"message": "if NVDA drops 3% today, buy 5"}
        )
        assert resp.status_code == 200
        data = resp.json()
        outcomes = data["rules"]
        assert len(outcomes) == 2

        created = outcomes[0]
        assert created["status"] == "created"
        assert set(created["rule"].keys()) == RULE_JSON_KEYS
        assert created["rule"]["ticker"] == "NVDA"
        assert created["rule"]["trigger_type"] == "day_change_pct_below"
        assert created["rule"]["threshold"] == -3
        assert created["rule"]["status"] == "active"
        assert created["rule"]["description"] == "Buy 5 NVDA when day change <= -3%"

        assert outcomes[1] == {
            "status": "failed",
            "ticker": "ZZZZ",
            "error": "Ticker not found in price cache",
        }

        # The created rule is committed and visible via GET /api/rules
        rules = (await chat_client.get("/api/rules")).json()["rules"]
        assert [r["id"] for r in rules] == [created["rule"]["id"]]

        # Stored assistant actions carry the same outcomes under "rules"
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        actions = messages[-1]["actions"]
        assert actions["rules"] == outcomes

    async def test_explicit_description_preserved(self, chat_client, monkeypatch):
        import litellm

        payload = {
            "message": "Done.",
            "rules": [
                {"ticker": "AAPL", "trigger_type": "price_below", "threshold": 150,
                 "side": "buy", "quantity": 2, "description": "Buy the big AAPL dip"},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post("/api/chat/", json={"message": "dip rule please"})
        assert resp.status_code == 200
        rule = resp.json()["rules"][0]["rule"]
        assert rule["description"] == "Buy the big AAPL dip"


@pytest.mark.asyncio
class TestMockPayloadUnchanged:
    """The LLM_MOCK deterministic reply stays byte-identical (E2E depends on it)."""

    async def test_mock_response_has_no_orders_or_rules_keys(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"message", "trades", "watchlist_changes"}
        assert data["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )

        # Stored actions likewise gain no orders/rules keys for this turn
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert set(messages[-1]["actions"].keys()) == {"trades", "watchlist_changes"}


@pytest.mark.asyncio
class TestSystemPromptDocumentsAgency:
    """The system prompt teaches the model the orders + rules vocabulary."""

    async def test_prompt_contains_capability_vocabulary(self, chat_client, monkeypatch):
        import litellm

        captured: dict = {}
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _capturing_completion_factory(captured))

        resp = await chat_client.post("/api/chat/", json={"message": "hi"})
        assert resp.status_code == 200

        system = captured["messages"][0]
        assert system["role"] == "system"
        content = system["content"]
        # Advanced orders guidance
        for token in ("'orders'", "stop_limit", "limit_price", "stop_price", "time_in_force"):
            assert token in content
        # Exact rules trigger vocabulary
        for trigger in (
            "price_above", "price_below", "day_change_pct_above", "day_change_pct_below"
        ):
            assert trigger in content
        assert "description" in content
        # Portfolio context still appended
        assert "Cash: $" in content
