"""Integration tests for the chat API endpoint.

Covers CHAT-01 through CHAT-06 requirements using the chat_client fixture
which sets LLM_MOCK=true and registers all routers including the chat router.

All tests use POST /api/chat/ (note trailing slash — the router uses
prefix="/api/chat" + @router.post("/") so the full path is /api/chat/).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
class TestChat:
    """Integration tests for POST /api/chat/ using the mock LLM."""

    async def test_chat_returns_structured_response(self, chat_client):
        """CHAT-01: POST /api/chat/ returns 200 with message, trades, watchlist_changes keys."""
        response = await chat_client.post("/api/chat/", json={"message": "Hello"})
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "trades" in data
        assert "watchlist_changes" in data

    async def test_response_schema_shape(self, chat_client):
        """CHAT-02: Response fields have correct types — message is str, trades/watchlist_changes are lists."""
        response = await chat_client.post("/api/chat/", json={"message": "What's my portfolio?"})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["message"], str)
        assert isinstance(data["trades"], list)
        assert isinstance(data["watchlist_changes"], list)

    async def test_mock_trade_executes(self, chat_client):
        """CHAT-03: After POST /api/chat/, AAPL position appears in portfolio and cash < 10000."""
        chat_resp = await chat_client.post("/api/chat/", json={"message": "Buy some AAPL"})
        assert chat_resp.status_code == 200

        portfolio_resp = await chat_client.get("/api/portfolio/")
        assert portfolio_resp.status_code == 200
        data = portfolio_resp.json()

        # Cash must have decreased (mock buys 5 shares of AAPL at seed price ~$190)
        assert data["cash"] < 10000.0

        # AAPL position must exist
        tickers = [p["ticker"] for p in data["positions"]]
        assert "AAPL" in tickers

    async def test_failed_trade_in_outcomes(self, chat_client):
        """CHAT-03: Trade failures are returned as outcome dicts (status=failed), not HTTP 500.

        The mock always tries to buy AAPL. We verify the response structure supports
        failed-trade outcomes by calling twice and confirming we still get 200 not 500.
        We also check that trade outcomes include a 'status' key — the structural requirement
        that failures are returned as dicts, never raised as HTTP errors.
        """
        # First call: buy 5 AAPL (succeeds)
        resp1 = await chat_client.post("/api/chat/", json={"message": "Buy AAPL"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        # Trade outcome dicts must have a 'status' key
        assert len(data1["trades"]) > 0
        for trade_outcome in data1["trades"]:
            assert "status" in trade_outcome

        # Second call: also succeeds (enough cash for another 5 shares)
        resp2 = await chat_client.post("/api/chat/", json={"message": "Buy AAPL again"})
        assert resp2.status_code == 200

    async def test_mock_watchlist_add(self, chat_client):
        """CHAT-04: After POST /api/chat/, PYPL appears in GET /api/watchlist/."""
        chat_resp = await chat_client.post("/api/chat/", json={"message": "Add PYPL to watchlist"})
        assert chat_resp.status_code == 200

        watchlist_resp = await chat_client.get("/api/watchlist/")
        assert watchlist_resp.status_code == 200
        data = watchlist_resp.json()

        tickers = [t["ticker"] for t in data["tickers"]]
        assert "PYPL" in tickers

    async def test_messages_persisted(self, chat_client):
        """CHAT-05: chat_messages rows are persisted; second request loads history without error."""
        # First request: persists user + assistant rows
        resp1 = await chat_client.post("/api/chat/", json={"message": "First message"})
        assert resp1.status_code == 200
        assert isinstance(resp1.json()["message"], str)

        # Second request: loads history (2 rows) and still returns 200
        resp2 = await chat_client.post("/api/chat/", json={"message": "Second message"})
        assert resp2.status_code == 200
        assert isinstance(resp2.json()["message"], str)

    async def test_history_loaded(self, chat_client):
        """CHAT-05: Two sequential POST /api/chat/ calls both return 200 with message key.

        The second call exercises the history load path (2 existing rows from first call).
        """
        resp1 = await chat_client.post("/api/chat/", json={"message": "Tell me about AAPL"})
        assert resp1.status_code == 200
        assert "message" in resp1.json()

        resp2 = await chat_client.post("/api/chat/", json={"message": "Should I buy more?"})
        assert resp2.status_code == 200
        assert "message" in resp2.json()

    async def test_mock_mode_deterministic(self, chat_client):
        """CHAT-06: LLM_MOCK=true returns the exact deterministic message string (D-06)."""
        response = await chat_client.post("/api/chat/", json={"message": "any message"})
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )

    async def test_get_chat_history(self, chat_client):
        """D-11: GET /api/chat/ returns 200 with messages array in ascending chronological order.

        Verifies:
        - GET /api/chat/ returns 200 with a JSON body containing a "messages" key
        - messages is a list
        - After seeding two messages, each item has keys role, content, actions, created_at
        - Messages are ordered ascending by created_at (earliest first)
        - actions is a parsed JSON object (dict) for assistant messages, not a raw string
        """
        import asyncio

        # Seed: post one chat message which creates user + assistant rows
        resp = await chat_client.post("/api/chat/", json={"message": "seed message"})
        assert resp.status_code == 200

        # Small delay to ensure distinct timestamps (both rows use datetime.now() in quick succession)
        await asyncio.sleep(0.01)

        # Now GET history
        response = await chat_client.get("/api/chat/")
        assert response.status_code == 200
        data = response.json()

        # Must have a "messages" key whose value is a list
        assert "messages" in data
        assert isinstance(data["messages"], list)

        # After one chat POST we have at least 2 rows (user + assistant)
        assert len(data["messages"]) >= 2

        # Each message has the expected keys
        for msg in data["messages"]:
            assert "role" in msg
            assert "content" in msg
            assert "actions" in msg
            assert "created_at" in msg

        # Messages are ordered ascending by created_at (earliest first)
        timestamps = [msg["created_at"] for msg in data["messages"]]
        assert timestamps == sorted(timestamps), "Messages must be in ascending chronological order"

        # The user message should appear before the assistant message
        roles = [msg["role"] for msg in data["messages"]]
        assert roles[0] == "user"
        assert roles[1] == "assistant"

        # actions for user message should be None
        user_msg = data["messages"][0]
        assert user_msg["actions"] is None

        # actions for assistant message should be a dict (parsed JSON), not a string
        asst_msg = data["messages"][1]
        if asst_msg["actions"] is not None:
            assert isinstance(asst_msg["actions"], dict), (
                f"actions must be a parsed dict, not {type(asst_msg['actions'])}"
            )


def _raw_completion_factory(content: str):
    """Build a litellm.completion stand-in returning raw (possibly invalid) content."""

    def fake_completion(*args, **kwargs):
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


@pytest.mark.asyncio
class TestChatMalformedLLMResponse:
    """Malformed LLM output must hit the graceful 500 path with zero side effects.

    Spec §12: "graceful handling of malformed responses". The except handler in
    chat.py catches parse/validation failures and returns HTTP 500 with
    {"error": "LLM unavailable"} — nothing may be persisted or executed.
    """

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(
                "Sure! I'd buy 5 shares of AAPL. (not JSON at all)",
                id="non-json-garbage",
            ),
            pytest.param(
                json.dumps({"trades": [], "watchlist_changes": []}),
                id="valid-json-missing-required-message",
            ),
        ],
    )
    async def test_malformed_llm_response_returns_500_and_writes_nothing(
        self, chat_client, monkeypatch, content
    ):
        import litellm

        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _raw_completion_factory(content))

        resp = await chat_client.post("/api/chat/", json={"message": "hello"})
        assert resp.status_code == 500
        assert resp.json() == {"error": "LLM unavailable"}

        # No partial chat_messages rows were written
        history = await chat_client.get("/api/chat/")
        assert history.json()["messages"] == []

        # No trades executed, cash untouched, no positions created
        portfolio = (await chat_client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == 10000.0
        assert portfolio["positions"] == []


def _capturing_completion_factory(captured: dict):
    """Build a litellm.completion stand-in that records kwargs and returns valid JSON."""

    def fake_completion(*args, **kwargs):
        captured.update(kwargs)
        content = json.dumps({"message": "ok", "trades": [], "watchlist_changes": []})
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


@pytest.mark.asyncio
class TestChatMarketEventContext:
    """The LLM prompt gains a 'Recent market events' section only when events exist.

    Extends the litellm.completion monkeypatch pattern to capture the
    ``messages`` argument and inspect the system prompt's portfolio context.
    """

    async def test_events_appear_in_llm_context(
        self, chat_client, fake_market_source, monkeypatch
    ):
        import litellm

        # Fire a market event through the cache funnel: AAPL +3% in one tick.
        cache = fake_market_source.price_cache
        price = cache.get_price("AAPL")
        cache.update("AAPL", price * 1.03)
        newest = cache.get_events(limit=1)[0]
        assert "surges" in newest.headline  # sanity: event actually recorded

        captured: dict = {}
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _capturing_completion_factory(captured))

        resp = await chat_client.post("/api/chat/", json={"message": "anything happening?"})
        assert resp.status_code == 200

        system_content = captured["messages"][0]["content"]
        assert captured["messages"][0]["role"] == "system"
        assert "Recent market events:" in system_content
        assert newest.headline in system_content
        assert " UTC" in system_content  # HH:MM:SS UTC timestamp prefix

    async def test_no_events_no_section(self, chat_client, monkeypatch):
        """Seed prices are first ticks (flat) — no events, so no section."""
        import litellm

        captured: dict = {}
        monkeypatch.setenv("LLM_MOCK", "false")
        monkeypatch.setattr(litellm, "completion", _capturing_completion_factory(captured))

        resp = await chat_client.post("/api/chat/", json={"message": "hello"})
        assert resp.status_code == 200

        system_content = captured["messages"][0]["content"]
        assert "Recent market events" not in system_content
        # The rest of the portfolio context is still present
        assert "Cash: $" in system_content
        assert "Watchlist:" in system_content
