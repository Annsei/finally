"""AI sentiment-context tests (P4 §1 — the "AI 引用" invariants).

Verifies that the market sentiment line rides ONLY the per-request context
assembly:

- ``_assemble_portfolio_context`` appends the English/Chinese sentiment line
  when the cache holds >= 2 tickers and appends NOTHING below the gate.
- The briefs event prompt gains the same line (real-LLM path; the LLM_MOCK
  brief text never changes).
- The ``SYSTEM_PROMPT`` / ``SYSTEM_PROMPT_ZH`` constants are byte-identical
  to their pre-P4 values (sha256 pins — the CN-3 red line).
- The default LLM_MOCK chat response is byte-identical to the pinned golden
  fixture even though the (unused-in-mock) context now carries the sentiment
  line.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.briefs import _generate_brief_text
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.models import MarketEvent
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.market.sentiment import compute_market_sentiment, sentiment_context_line
from app.routes.chat import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ZH,
    _assemble_portfolio_context,
    create_chat_router,
)

GOLDEN_DIR = Path(__file__).parent / "golden"

# sha256 pins of the frozen system prompts (recomputed for D4 §2.3, which
# appends the 'research' bullet to both constants — the ONLY permitted
# prompt evolution since the P4 pins). ANY other byte change to either
# constant fails here — the P4 §1 sentiment line must still ride the
# per-request context instead.
SYSTEM_PROMPT_SHA256 = (
    "64a589ae7ee2e041450b3fb624b745c760e2a07cdbb1108a43b5de016eda1af2"
)
SYSTEM_PROMPT_ZH_SHA256 = (
    "ea443b3d9a86103fccb53ba40f05f40939d2c1595c38f2562487bfd723959093"
)


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "context.db")
    init_db(db_path)
    return db_path


def _two_ticker_cache() -> PriceCache:
    cache = PriceCache()
    cache.update("AAPL", 110.0, timestamp=1_200_000, prev_close=100.0)
    cache.update("MSFT", 90.0, timestamp=1_200_000, prev_close=100.0)
    return cache


class TestChatContextSentimentLine:
    def test_english_line_appended_at_end(self, tmp_path):
        db_path = _make_db(tmp_path)
        cache = _two_ticker_cache()
        conn = get_conn(db_path)
        try:
            context = _assemble_portfolio_context(conn, cache)
        finally:
            conn.close()
        expected = sentiment_context_line(compute_market_sentiment(cache))
        assert expected is not None
        assert expected.startswith("Market sentiment: ")
        assert context.endswith(f"\n{expected}")

    def test_chinese_line_when_zh(self, tmp_path):
        db_path = _make_db(tmp_path)
        cache = _two_ticker_cache()
        conn = get_conn(db_path)
        try:
            context = _assemble_portfolio_context(conn, cache, zh=True)
        finally:
            conn.close()
        expected = sentiment_context_line(compute_market_sentiment(cache), zh=True)
        assert expected is not None
        assert expected.startswith("市场情绪：")
        assert context.endswith(f"\n{expected}")
        assert "Market sentiment:" not in context

    def test_below_sample_gate_appends_nothing(self, tmp_path):
        db_path = _make_db(tmp_path)
        cache = PriceCache()
        cache.update("AAPL", 110.0, timestamp=1_200_000, prev_close=100.0)  # one ticker
        conn = get_conn(db_path)
        try:
            context = _assemble_portfolio_context(conn, cache)
            context_zh = _assemble_portfolio_context(conn, cache, zh=True)
        finally:
            conn.close()
        for text in (context, context_zh):
            assert "Market sentiment" not in text
            assert "市场情绪" not in text


def _event(ticker: str = "AAPL") -> MarketEvent:
    return MarketEvent(
        id="e1",
        ticker=ticker,
        headline=f"{ticker} surges +3.0% in sudden move",
        change_percent=3.0,
        direction="up",
        timestamp=1_200_000.0,
    )


def _capture_completion(monkeypatch) -> dict:
    captured: dict = {}

    def fake_completion(*args, **kwargs):
        captured.update(kwargs)
        msg = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return captured


@pytest.mark.asyncio
class TestBriefEventContextSentimentLine:
    async def test_line_appended_to_brief_prompt(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        cache = _two_ticker_cache()
        captured = _capture_completion(monkeypatch)

        await _generate_brief_text(cache, None, _event())
        user_content = captured["messages"][1]["content"]
        expected = sentiment_context_line(compute_market_sentiment(cache))
        assert user_content.startswith("Market event: ")
        assert user_content.endswith(f"\n{expected}")

    async def test_chinese_line_on_cn_profile(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        cache = _two_ticker_cache()
        captured = _capture_completion(monkeypatch)

        await _generate_brief_text(cache, None, _event(), CN_PROFILE)
        user_content = captured["messages"][1]["content"]
        expected = sentiment_context_line(compute_market_sentiment(cache), zh=True)
        assert user_content.endswith(f"\n{expected}")

    async def test_below_sample_gate_appends_nothing(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        cache = PriceCache()
        cache.update("AAPL", 110.0, timestamp=1_200_000, prev_close=100.0)
        captured = _capture_completion(monkeypatch)

        await _generate_brief_text(cache, None, _event())
        user_content = captured["messages"][1]["content"]
        assert "Market sentiment" not in user_content
        assert user_content.endswith("Day change: +10.00%")

    async def test_mock_brief_text_unchanged(self, monkeypatch):
        # LLM_MOCK output must stay byte-identical — sentiment only rides
        # the real-LLM prompt, never the deterministic mock text.
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = _two_ticker_cache()
        text = await _generate_brief_text(cache, None, _event())
        assert text == "[MOCK BRIEF] AAPL moved +3.0% — review your exposure."


class TestSystemPromptFrozen:
    def test_system_prompt_bytes_unchanged(self):
        digest = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert digest == SYSTEM_PROMPT_SHA256

    def test_system_prompt_zh_bytes_unchanged(self):
        digest = hashlib.sha256(SYSTEM_PROMPT_ZH.encode("utf-8")).hexdigest()
        assert digest == SYSTEM_PROMPT_ZH_SHA256


@pytest_asyncio.fixture
async def mock_chat_client(tmp_path, monkeypatch):
    """US chat router, LLM_MOCK=true, deterministic ids — the golden setup.

    The cache carries the full SEED_PRICES board (>= 2 tickers), so the
    per-request context DOES include the P4 sentiment line — proving the
    mock response below stays byte-identical regardless.
    """
    db_file = str(tmp_path / "mock_regress.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(db_file)

    counter = {"n": 0}

    def fake_uuid4():
        counter["n"] += 1
        return f"00000000-0000-0000-0000-{counter['n']:012d}"

    monkeypatch.setattr("uuid.uuid4", fake_uuid4)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_chat_router(price_cache, db_file))
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
class TestDefaultMockByteRegression:
    async def test_default_mock_response_matches_golden(self, mock_chat_client):
        resp = await mock_chat_client.post("/api/chat/", json={"message": "hello there"})
        assert resp.status_code == 200
        golden = json.loads(
            (GOLDEN_DIR / "chat_mock_default.json").read_text(encoding="utf-8")
        )

        def canonical(payload) -> str:
            return json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )

        assert canonical(resp.json()) == canonical(golden)
