"""CN-3 §5: AI system prompts and deterministic LLM_MOCK text are localized by
the active market profile's locale.

Invariants under test:
- profile None / locale 'en-US' -> the existing English prompts and English
  mock strings are byte-identical to today (the other existing chat/briefs/
  review suites assert this too; these tests add explicit guards in one place).
- locale 'zh-CN' -> Chinese prompts that inject A-share constraints (整手 /
  T+1 / ¥ / 涨停/跌停 / 印花税) and Chinese deterministic mock branches
  (including the '回测'/'backtest' keyword branch).
- the structured-output schema keys and enum values stay ENGLISH in both
  languages (trades / orders / rules / backtests / watchlist_changes) so the
  frontend needs zero adaptation.
- briefs_watch_loop threads the profile down to the brief/narrative passes.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.briefs import (
    BRIEF_SYSTEM_PROMPT,
    BRIEF_SYSTEM_PROMPT_ZH,
    NARRATIVE_SYSTEM_PROMPT,
    NARRATIVE_SYSTEM_PROMPT_ZH,
    BriefWatcherState,
    NarrativeEnricherState,
    _generate_brief_text,
    _generate_narrative_text,
    _locale_is_zh,
    process_events_for_briefs_once,
    process_events_for_narratives_once,
)
from app.db.connection import init_db
from app.market import PriceCache
from app.market.models import MarketEvent
from app.market.profiles import CN_PROFILE, US_PROFILE
from app.routes.chat import (
    REVIEW_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT_ZH,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ZH,
    ChatResponse,
    create_chat_router,
)
from tests.test_briefs import BASE_TS, NOW, _chat_rows, _fire_event
from tests.test_chat_agent import _capturing_completion_factory

# Deterministic Chinese mock strings (must match app source byte-for-byte).
# Only the message is Chinese — the action arrays mirror the US mocks (CN-3:
# "only the message/narrative language changes"), so under the CN profile the
# buy-5-AAPL non-lot order is still rejected by the 整手 rule.
CN_DEFAULT_MESSAGE = "已将 PYPL 加入你的自选，并为你买入 5 股 AAPL。"
CN_BACKTEST_MESSAGE = "[模拟] 回测完成：已在 20 个模拟交易日上测试 NVDA 逢跌买入策略。"


def _cn_review_text(trade_count: int) -> str:
    return (
        f"[模拟复盘] 你今天进行了 {trade_count} 笔交易。请在下一个交易时段"
        "开始前检视你的持仓与风险。"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cn_mock_client(tmp_path, monkeypatch):
    """Chat router built with the CN profile, LLM_MOCK=true (Chinese mocks).

    Seeded with the CN universe prices and ¥100k seed cash so the Chinese
    default mock's one-lot (100-share) 000858 buy actually executes.
    """
    db_file = str(tmp_path / "cn_mock.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(
        db_file,
        seed_cash=CN_PROFILE.seed_cash,
        default_watchlist=list(CN_PROFILE.universe.default_watchlist),
    )

    price_cache = PriceCache()
    for ticker, price in CN_PROFILE.universe.seed_prices.items():
        price_cache.update(ticker, price)
    # The default mock buys 5 AAPL; price it so the buy reaches (and fails) the
    # CN 整手 lot check rather than an unknown-ticker error.
    price_cache.update("AAPL", 190.0)

    test_app = FastAPI()
    test_app.include_router(
        create_chat_router(price_cache, db_file, profile=CN_PROFILE)
    )
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def cn_real_client(tmp_path, monkeypatch):
    """CN-profile chat router on the real LLM path (LLM_MOCK=false)."""
    db_file = str(tmp_path / "cn_real.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "false")
    init_db(db_file, seed_cash=CN_PROFILE.seed_cash)

    price_cache = PriceCache()
    for ticker, price in CN_PROFILE.universe.seed_prices.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(
        create_chat_router(price_cache, db_file, profile=CN_PROFILE)
    )
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def us_real_client(tmp_path, monkeypatch):
    """No-profile (US default) chat router on the real LLM path."""
    db_file = str(tmp_path / "us_real.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "false")
    init_db(db_file)

    price_cache = PriceCache()
    price_cache.update("AAPL", 190.0)

    test_app = FastAPI()
    test_app.include_router(create_chat_router(price_cache, db_file))
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Prompt selection (real path — capture the system message actually sent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatPromptSelection:
    async def test_cn_locale_sends_chinese_system_prompt(self, cn_real_client, monkeypatch):
        import litellm

        captured: dict = {}
        monkeypatch.setattr(
            litellm, "completion", _capturing_completion_factory(captured)
        )
        resp = await cn_real_client.post("/api/chat/", json={"message": "hi"})
        assert resp.status_code == 200
        system_content = captured["messages"][0]["content"]
        # The Chinese prompt is used verbatim; the English one is absent.
        assert system_content.startswith(SYSTEM_PROMPT_ZH)
        assert SYSTEM_PROMPT not in system_content

    async def test_us_locale_sends_english_system_prompt_byte_identical(
        self, us_real_client, monkeypatch
    ):
        import litellm

        captured: dict = {}
        monkeypatch.setattr(
            litellm, "completion", _capturing_completion_factory(captured)
        )
        resp = await us_real_client.post("/api/chat/", json={"message": "hi"})
        assert resp.status_code == 200
        system_content = captured["messages"][0]["content"]
        # No profile -> the English constant is used byte-for-byte.
        assert system_content.startswith(SYSTEM_PROMPT)
        assert SYSTEM_PROMPT_ZH not in system_content

    async def test_review_prompt_selection_by_locale(
        self, cn_real_client, us_real_client, monkeypatch
    ):
        import litellm

        cn_captured: dict = {}
        monkeypatch.setattr(
            litellm, "completion", _capturing_completion_factory(cn_captured)
        )
        cn_resp = await cn_real_client.post("/api/chat/review")
        assert cn_resp.status_code == 200
        assert cn_captured["messages"][0]["content"] == REVIEW_SYSTEM_PROMPT_ZH

        us_captured: dict = {}
        monkeypatch.setattr(
            litellm, "completion", _capturing_completion_factory(us_captured)
        )
        us_resp = await us_real_client.post("/api/chat/review")
        assert us_resp.status_code == 200
        assert us_captured["messages"][0]["content"] == REVIEW_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Chinese deterministic mock branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChineseChatMocks:
    async def test_cn_default_mock_message_chinese_actions_mirror_us(self, cn_mock_client):
        resp = await cn_mock_client.post("/api/chat/", json={"message": "帮我操作一下"})
        assert resp.status_code == 200
        data = resp.json()
        # Message is Chinese; the actions mirror the US mock (buy 5 AAPL, add
        # PYPL) so the CN profile still governs execution: 5 shares is not a
        # whole board lot, so the buy is rejected by the 整手 rule.
        assert data["message"] == CN_DEFAULT_MESSAGE
        assert len(data["trades"]) == 1
        assert data["trades"][0]["ticker"] == "AAPL"
        assert data["trades"][0]["status"] == "failed"
        assert data["trades"][0]["error"] == "A股买入须为 100 股的整数倍"
        assert data["watchlist_changes"][0]["status"] == "added"
        assert data["watchlist_changes"][0]["ticker"] == "PYPL"

    async def test_cn_backtest_keyword_branch_english_keyword(self, cn_mock_client):
        resp = await cn_mock_client.post("/api/chat/", json={"message": "run a backtest"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == CN_BACKTEST_MESSAGE
        assert "backtests" in data and len(data["backtests"]) == 1

    async def test_cn_backtest_keyword_branch_chinese_huice(self, cn_mock_client):
        # The Chinese keyword '回测' also routes to the backtest branch.
        resp = await cn_mock_client.post("/api/chat/", json={"message": "回测一下这个策略"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == CN_BACKTEST_MESSAGE
        assert "backtests" in data

    async def test_cn_review_mock_is_chinese(self, cn_mock_client):
        resp = await cn_mock_client.post("/api/chat/review")
        assert resp.status_code == 200
        assert resp.json() == {"message": _cn_review_text(0), "kind": "review"}


# ---------------------------------------------------------------------------
# English mock byte-identity (regression guard for the existing E2E command)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEnglishMocksUnchanged:
    async def test_default_english_mock_byte_identical(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "anything"})
        assert resp.status_code == 200
        assert resp.json()["message"] == (
            "I've added PYPL to your watchlist and bought 5 shares of AAPL for you."
        )

    async def test_backtest_english_mock_byte_identical(self, chat_client):
        resp = await chat_client.post("/api/chat/", json={"message": "backtest it"})
        assert resp.status_code == 200
        assert resp.json()["message"] == (
            "[MOCK] Backtest complete: NVDA dip-buy strategy tested over 20 simulated days."
        )

    async def test_english_review_mock_byte_identical(self, chat_client):
        resp = await chat_client.post("/api/chat/review")
        assert resp.status_code == 200
        assert resp.json()["message"] == (
            "[MOCK REVIEW] You made 0 trades today. "
            "Review your positions and risk before the next session."
        )


# ---------------------------------------------------------------------------
# Prompt content: A-share injection + English schema key stability
# ---------------------------------------------------------------------------


class TestPromptContent:
    def test_zh_prompt_injects_ashare_constraints(self):
        for token in ("整手", "100 股", "T+1", "¥", "涨停", "跌停", "印花税"):
            assert token in SYSTEM_PROMPT_ZH

    def test_zh_prompt_keeps_english_schema_keys_and_enums(self):
        for key in (
            "'trades'",
            "'orders'",
            "'rules'",
            "'backtests'",
            "'watchlist_changes'",
        ):
            assert key in SYSTEM_PROMPT_ZH
        for enum in (
            "price_above",
            "price_below",
            "day_change_pct_above",
            "day_change_pct_below",
            "'limit'",
            "'stop'",
            "'stop_limit'",
            "take_profit_pct",
            "stop_loss_pct",
            "'add'",
            "'remove'",
        ):
            assert enum in SYSTEM_PROMPT_ZH

    def test_chat_response_schema_fields_are_stable(self):
        assert set(ChatResponse.model_fields) == {
            "message",
            "trades",
            "watchlist_changes",
            "orders",
            "rules",
            "backtests",
        }

    def test_english_prompt_constants_unchanged(self):
        # The English constants remain distinct from the Chinese variants and
        # keep their signature phrases (byte-identity guard alongside the mock
        # assertions above).
        assert SYSTEM_PROMPT != SYSTEM_PROMPT_ZH
        assert REVIEW_SYSTEM_PROMPT != REVIEW_SYSTEM_PROMPT_ZH
        assert SYSTEM_PROMPT.startswith(
            "You are FinAlly, an AI trading assistant. Be concise and data-driven. "
        )
        assert REVIEW_SYSTEM_PROMPT == (
            "You are FinAlly, an AI trading assistant, writing the user's daily "
            "review. Reply in plain text (no JSON, no markdown headings), 3-6 "
            "sentences: what happened today, the best and worst decision, and one "
            "concrete suggestion. Be concise and data-driven."
        )

    def test_zh_briefs_prompts_localized(self):
        assert BRIEF_SYSTEM_PROMPT_ZH != BRIEF_SYSTEM_PROMPT
        assert NARRATIVE_SYSTEM_PROMPT_ZH != NARRATIVE_SYSTEM_PROMPT
        for token in ("整手", "印花税", "卖空"):
            assert token in BRIEF_SYSTEM_PROMPT_ZH
        assert "模拟" in NARRATIVE_SYSTEM_PROMPT_ZH


# ---------------------------------------------------------------------------
# Briefs / narratives: locale helper + mock branches + profile threading
# ---------------------------------------------------------------------------


def _event(ticker: str = "AAPL", pct: float = 3.0) -> MarketEvent:
    return MarketEvent(
        id="e1",
        ticker=ticker,
        headline=f"{ticker} surges {pct:+.1f}% in sudden move",
        change_percent=pct,
        direction="up" if pct >= 0 else "down",
        timestamp=BASE_TS,
    )


class TestLocaleHelper:
    def test_locale_is_zh(self):
        assert _locale_is_zh(None) is False
        assert _locale_is_zh(US_PROFILE) is False
        assert _locale_is_zh(CN_PROFILE) is True


@pytest.mark.asyncio
class TestBriefNarrativeMocks:
    async def test_brief_mock_chinese_for_cn(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = PriceCache()
        text = await _generate_brief_text(cache, None, _event(), CN_PROFILE)
        assert text == "[模拟简报] AAPL 异动 +3.0% —— 请检视你的持仓。"

    async def test_brief_mock_english_default_and_us(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = PriceCache()
        expected = "[MOCK BRIEF] AAPL moved +3.0% — review your exposure."
        assert await _generate_brief_text(cache, None, _event()) == expected
        assert await _generate_brief_text(cache, None, _event(), US_PROFILE) == expected

    async def test_narrative_mock_chinese_for_cn(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = PriceCache()
        event = _event("NVDA")
        text = await _generate_narrative_text(cache, event, CN_PROFILE)
        assert text == f"[模拟新闻] {event.headline}"

    async def test_narrative_mock_english_default(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = PriceCache()
        event = _event("NVDA")
        assert await _generate_narrative_text(cache, event) == f"[MOCK NEWS] {event.headline}"

    async def test_brief_real_path_selects_prompt_by_locale(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        cache = PriceCache()
        cache.update("AAPL", 100.0)

        captured: dict = {}

        def fake_completion(*args, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace

            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        import litellm

        monkeypatch.setattr(litellm, "completion", fake_completion)

        await _generate_brief_text(cache, None, _event(), CN_PROFILE)
        assert captured["messages"][0]["content"] == BRIEF_SYSTEM_PROMPT_ZH

        captured.clear()
        await _generate_brief_text(cache, None, _event())
        assert captured["messages"][0]["content"] == BRIEF_SYSTEM_PROMPT

    async def test_narrative_real_path_selects_prompt_by_locale(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        cache = PriceCache()
        cache.update("AAPL", 100.0)

        captured: dict = {}

        def fake_completion(*args, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace

            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        import litellm

        monkeypatch.setattr(litellm, "completion", fake_completion)

        await _generate_narrative_text(cache, _event(), CN_PROFILE)
        assert captured["messages"][0]["content"] == NARRATIVE_SYSTEM_PROMPT_ZH

        captured.clear()
        await _generate_narrative_text(cache, _event())
        assert captured["messages"][0]["content"] == NARRATIVE_SYSTEM_PROMPT


@pytest.mark.asyncio
class TestBriefsProfileThreading:
    async def test_briefs_pass_threads_profile_to_chinese_mock(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        db_file = str(tmp_path / "briefs_cn.db")
        init_db(db_file)  # default watchlist includes NVDA
        cache = PriceCache()
        cache.update("NVDA", 100.0, timestamp=BASE_TS)
        _fire_event(cache, "NVDA", BASE_TS + 10)

        counts = await process_events_for_briefs_once(
            cache, db_file, BriefWatcherState(), CN_PROFILE, now=NOW
        )
        assert counts["briefed"] == 1
        rows = [r for r in _chat_rows(db_file) if r["kind"] == "brief"]
        assert rows[0]["content"].startswith("[模拟简报]")
        assert "NVDA" in rows[0]["content"]

    async def test_narratives_pass_threads_profile_to_chinese_mock(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LLM_MOCK", "true")
        cache = PriceCache()
        cache.update("NVDA", 100.0, timestamp=BASE_TS)
        _fire_event(cache, "NVDA", BASE_TS + 10)

        counts = await process_events_for_narratives_once(
            cache, NarrativeEnricherState(), CN_PROFILE, now=NOW
        )
        assert counts["enriched"] == 1
        event = cache.get_events()[0]
        assert event.narrative.startswith("[模拟新闻]")

    async def test_briefs_pass_default_profile_stays_english(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        db_file = str(tmp_path / "briefs_us.db")
        init_db(db_file)
        cache = PriceCache()
        cache.update("NVDA", 100.0, timestamp=BASE_TS)
        _fire_event(cache, "NVDA", BASE_TS + 10)

        # No profile arg -> English mock, unchanged.
        counts = await process_events_for_briefs_once(
            cache, db_file, BriefWatcherState(), now=NOW
        )
        assert counts["briefed"] == 1
        rows = [r for r in _chat_rows(db_file) if r["kind"] == "brief"]
        assert rows[0]["content"] == "[MOCK BRIEF] NVDA moved +3.0% — review your exposure."
