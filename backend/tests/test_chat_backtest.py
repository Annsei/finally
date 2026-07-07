"""Chat backtest integration tests (M5): the ``backtests`` array runs the engine.

Covers:
- LLM_MOCK messages containing "backtest" return the deterministic NVDA
  dip-buy mock, run the engine, and carry a compact completed outcome
  (config + stats, never curves/trades) in both the response and the stored
  actions
- the default LLM_MOCK payload stays byte-identical (no 'backtests' key)
- a failing instruction (unknown ticker) yields status='failed' without
  aborting the turn
- the system prompt documents the backtests vocabulary
"""

from __future__ import annotations

import pytest

from app.routes.chat import SYSTEM_PROMPT
from tests.test_backtest import CONFIG_KEYS, STATS_KEYS
from tests.test_chat_agent import _fake_completion_factory

OUTCOME_KEYS = {"status", "ticker", "config", "stats"}

MOCK_BACKTEST_MESSAGE = (
    "[MOCK] Backtest complete: NVDA dip-buy strategy tested over 20 simulated days."
)


@pytest.mark.asyncio
class TestChatRunsBacktests:
    async def test_mock_backtest_message_runs_engine(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "run a backtest please"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == MOCK_BACKTEST_MESSAGE
        assert data["trades"] == []
        assert data["watchlist_changes"] == []

        outcomes = data["backtests"]
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert set(outcome.keys()) == OUTCOME_KEYS  # compact: no curves/trades
        assert outcome["status"] == "completed"
        assert outcome["ticker"] == "NVDA"
        assert set(outcome["config"].keys()) == CONFIG_KEYS
        assert outcome["config"]["trigger_type"] == "day_change_pct_below"
        assert outcome["config"]["threshold"] == -3
        assert outcome["config"]["days"] == 20
        assert outcome["config"]["runs"] == 1
        assert isinstance(outcome["config"]["seed"], int)  # drawn -> reproducible
        assert set(outcome["stats"].keys()) == STATS_KEYS

        # Stored assistant actions carry the same compact outcomes
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert messages[-1]["role"] == "assistant"
        actions = messages[-1]["actions"]
        assert set(actions.keys()) == {"trades", "watchlist_changes", "backtests"}
        assert actions["backtests"] == outcomes

    async def test_mock_backtest_trigger_is_case_insensitive(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "BACKTEST my NVDA idea"})
        assert resp.status_code == 200
        assert "backtests" in resp.json()

    async def test_default_mock_payload_unchanged(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "buy some apple"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"message", "trades", "watchlist_changes"}
        assert data["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert set(messages[-1]["actions"].keys()) == {"trades", "watchlist_changes"}

    async def test_failed_instruction_is_non_fatal(self, chat_client, monkeypatch):
        import litellm

        payload = {
            "message": "Tested both.",
            "backtests": [
                # unknown ticker -> per-instruction failure, non-fatal
                {"ticker": "ZZZZ", "trigger_type": "price_above",
                 "threshold": 100, "quantity": 1},
                {"ticker": "NVDA", "trigger_type": "price_above",
                 "threshold": 1, "quantity": 1, "days": 5},
            ],
        }
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))

        resp = await chat_client.post("/api/chat/", json={"message": "test these"})
        assert resp.status_code == 200
        outcomes = resp.json()["backtests"]
        assert outcomes[0] == {
            "status": "failed",
            "ticker": "ZZZZ",
            "error": "Ticker not found",
        }
        assert outcomes[1]["status"] == "completed"
        assert outcomes[1]["config"]["days"] == 5

        # The failed instruction did not abort the turn — both messages stored
        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert len(messages) == 2
        assert messages[-1]["actions"]["backtests"] == outcomes


class TestSystemPromptDocumentsBacktests:
    def test_prompt_contains_backtest_vocabulary(self):
        assert "'backtests'" in SYSTEM_PROMPT
        for token in ("take_profit_pct", "stop_loss_pct", "'backtest'", "回测"):
            assert token in SYSTEM_PROMPT
        assert "five action arrays" in SYSTEM_PROMPT
