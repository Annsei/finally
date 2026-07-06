"""Tests for the daily AI review endpoint (M2.4, Task C).

POST /api/chat/review — no body. Uses the chat_client fixture (LLM_MOCK=true)
and verifies storage through GET /api/chat/ (which now returns ``kind``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _mock_review_text(trade_count: int) -> str:
    return (
        f"[MOCK REVIEW] You made {trade_count} trades today. "
        "Review your positions and risk before the next session."
    )


@pytest.mark.asyncio
class TestDailyReviewMock:
    """LLM_MOCK path: deterministic text, one stored row, fixed contract."""

    async def test_review_returns_200_and_stores_one_row(self, chat_client):
        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 200
        assert resp.json() == {"message": _mock_review_text(0), "kind": "review"}

        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        reviews = [m for m in messages if m["kind"] == "review"]
        assert len(reviews) == 1
        assert reviews[0]["role"] == "assistant"
        assert reviews[0]["actions"] is None
        assert reviews[0]["content"] == _mock_review_text(0)
        # Nothing else was stored
        assert len(messages) == 1

    async def test_review_interpolates_todays_trade_count(self, chat_client):
        for ticker in ("AAPL", "MSFT"):
            trade = await chat_client.post(
                "/api/portfolio/trade",
                json={"ticker": ticker, "quantity": 1, "side": "buy"},
            )
            assert trade.status_code == 200

        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 200
        assert resp.json()["message"] == _mock_review_text(2)

    async def test_each_call_stores_a_new_review_row(self, chat_client):
        assert (await chat_client.post("/api/chat/review")).status_code == 200
        assert (await chat_client.post("/api/chat/review")).status_code == 200

        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert len([m for m in messages if m["kind"] == "review"]) == 2


@pytest.mark.asyncio
class TestDailyReviewRealPath:
    """Real-LLM path (monkeypatched litellm): prompt shape and failure handling."""

    async def test_llm_failure_returns_500_and_stores_nothing(self, chat_client, monkeypatch):
        import litellm

        monkeypatch.setenv("LLM_MOCK", "false")

        def exploding_completion(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(litellm, "completion", exploding_completion)

        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 500
        assert resp.json() == {"error": "LLM unavailable"}

        messages = (await chat_client.get("/api/chat/")).json()["messages"]
        assert messages == []

    async def test_empty_llm_content_returns_500_and_stores_nothing(
        self, chat_client, monkeypatch
    ):
        import litellm

        monkeypatch.setenv("LLM_MOCK", "false")

        def empty_completion(*args, **kwargs):
            message = SimpleNamespace(content="   ")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        monkeypatch.setattr(litellm, "completion", empty_completion)

        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 500
        assert resp.json() == {"error": "LLM unavailable"}
        assert (await chat_client.get("/api/chat/")).json()["messages"] == []

    async def test_review_prompt_is_plain_text_with_daily_context(
        self, chat_client, monkeypatch
    ):
        import litellm

        # One real trade so the context has a trades line.
        trade = await chat_client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "buy"}
        )
        assert trade.status_code == 200

        captured: dict = {}

        def capturing_completion(*args, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content="Solid day; consider trimming AAPL.")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", capturing_completion)

        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 200
        assert resp.json() == {
            "message": "Solid day; consider trimming AAPL.",
            "kind": "review",
        }

        # Plain-text call: no structured-output response_format.
        assert "response_format" not in captured

        user_prompt = captured["messages"][-1]["content"]
        assert "Today's trades" in user_prompt
        assert "buy 2 AAPL" in user_prompt
        assert "Cash: $" in user_prompt
        assert "Lifetime realized P&L" in user_prompt
        assert "Rules fired today" in user_prompt
        assert "day P&L" in user_prompt
