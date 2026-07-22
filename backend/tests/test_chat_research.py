"""Chat `research` action tests (D4 §2.1/§2.4/§2.5).

Covers:
- the deterministic LLM_MOCK research branch (en + zh): one instruction,
  120 trading days, 3 template candidates with lot-safe cash_pct sizing —
  AAPL on the US router, 600519 on the CN one (both ship in the committed
  sample bars, so the whole turn is offline and RNG-free)
- the response/persisted `research` outcome: ranked candidates carrying
  strategy_id/run_id/score/stats, drafts owned by the requesting user, runs
  visible in GET /api/backtest/runs with the "Research: " label
- keyword precedence: 'research' beats the 'strategy' branch (the E2E
  message contains both tokens) and '研究' beats '策略'
- the `research` key is ABSENT from the default and backtest mock responses
  (and their stored actions) — the non-empty-only rule
- faked-LLM turns: per-instruction isolation (a bad batch never aborts the
  turn) and the single-commit invariant (handler writes ride the turn)
- the system prompts advertise the research array in both languages
  (token-presence style, mirroring test_chat_strategies.py)
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.history import SampleProvider, upsert_daily_bars
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.research import RUN_LABEL_PREFIX
from app.routes.backtest_runs import create_backtest_runs_router
from app.routes.chat import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ZH,
    ChatResearchTurnResponse,
    ChatTurnResponse,
    create_chat_router,
)
from app.routes.strategies import create_strategies_router

# The E2E contract messages (§4) — the US one deliberately contains
# 'strategies' so it exercises the research-before-strategy precedence.
US_RESEARCH_MESSAGE = "Research momentum strategies for AAPL"
ZH_RESEARCH_MESSAGE = "帮我研究一下 600519 的策略"

MOCK_TEMPLATES = ("ma_golden_cross", "rsi_rebound", "momentum_breakout")


def _seed_sample_bars(db_file: str, market: str, ticker: str) -> None:
    """Store the committed sample series (fixed dates — deterministic)."""
    bars = SampleProvider(market).fetch_daily(
        ticker, date(2020, 1, 1), date(2026, 7, 1)
    )
    conn = get_conn(db_file)
    try:
        upsert_daily_bars(
            conn,
            market=market,
            ticker=ticker,
            bars=bars,
            source="sample",
            fetched_at="2026-07-01T00:00:00+00:00",
        )
        conn.commit()
    finally:
        conn.close()


async def _build_app(tmp_path, monkeypatch, *, profile=None):
    db_file = str(tmp_path / "chat_research.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    if profile is None:
        init_db(db_file)
        _seed_sample_bars(db_file, "us", "AAPL")
    else:
        init_db(
            db_file,
            seed_cash=profile.seed_cash,
            default_watchlist=list(profile.universe.default_watchlist),
        )
        _seed_sample_bars(db_file, "cn", "600519")

    price_cache = PriceCache()
    seeds = SEED_PRICES if profile is None else profile.universe.seed_prices
    for ticker, price in seeds.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_chat_router(price_cache, db_file, profile=profile))
    test_app.include_router(create_strategies_router(price_cache, db_file, profile))
    test_app.include_router(
        create_backtest_runs_router(price_cache, db_file, profile=profile)
    )
    return test_app, db_file, price_cache


@pytest_asyncio.fixture
async def us_chat(tmp_path, monkeypatch):
    test_app, db_file, price_cache = await _build_app(tmp_path, monkeypatch)
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, db_file=db_file, price_cache=price_cache)


@pytest_asyncio.fixture
async def cn_chat(tmp_path, monkeypatch):
    test_app, db_file, price_cache = await _build_app(
        tmp_path, monkeypatch, profile=CN_PROFILE
    )
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, db_file=db_file, price_cache=price_cache)


def _db_strategies(db_file: str) -> list:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT * FROM strategies ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    finally:
        conn.close()


def _assert_ranked_batch(outcome: dict, ticker: str) -> None:
    """Shared shape assertions for one completed 3-candidate mock batch."""
    assert outcome["status"] == "completed"
    assert outcome["ticker"] == ticker
    assert outcome["days"] == 120
    candidates = outcome["candidates"]
    assert len(candidates) == 3
    assert all(c["status"] == "completed" for c in candidates)
    assert sorted(c["rank"] for c in candidates) == [1, 2, 3]
    for candidate in candidates:
        assert candidate["strategy_id"]
        assert candidate["run_id"]
        assert candidate["hypothesis"]
        assert candidate["score"] == round(
            candidate["stats"]["total_return_pct"]
            - 0.5 * candidate["stats"]["max_drawdown_pct"],
            2,
        )
    # Data-independent recommendation invariant: the rank-1 candidate is
    # recommended IFF it traded, otherwise the recommendation is null.
    top = next(c for c in candidates if c["rank"] == 1)
    if top["traded"]:
        assert outcome["recommended_strategy_id"] == top["strategy_id"]
    else:
        assert outcome["recommended_strategy_id"] is None


# ---------------------------------------------------------------------------
# LLM_MOCK research branch — US
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMockResearchBranchUS:
    async def test_research_message_returns_ranked_candidates(self, us_chat):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": US_RESEARCH_MESSAGE}
        )
        assert resp.status_code == 200
        body = resp.json()
        # 'research' won over the 'strategy' keyword in the same message.
        assert "strategies" not in body
        outcome = body["research"][0]
        _assert_ranked_batch(outcome, "AAPL")
        names = [c["name"] for c in outcome["candidates"]]
        assert names == ["Golden Cross", "RSI Rebound", "Momentum Breakout"]

    async def test_drafts_owned_by_requesting_user(self, us_chat):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": US_RESEARCH_MESSAGE}
        )
        outcome = resp.json()["research"][0]
        rows = _db_strategies(us_chat.db_file)
        assert len(rows) == 3
        assert {row["id"] for row in rows} == {
            c["strategy_id"] for c in outcome["candidates"]
        }
        for row in rows:
            assert row["status"] == "draft"  # research never deploys
            assert row["user_id"] == "default"  # the cookie-less default user
        assert {row["template"] for row in rows} == set(MOCK_TEMPLATES)
        # Contract §2.4: every mock candidate sizes with lot-safe cash_pct 20.
        for row in rows:
            assert json.loads(row["sizing"]) == {"mode": "cash_pct", "pct": 20.0}

    async def test_runs_visible_in_run_library(self, us_chat):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": US_RESEARCH_MESSAGE}
        )
        outcome = resp.json()["research"][0]
        runs = (await us_chat.client.get("/api/backtest/runs")).json()["runs"]
        assert len(runs) == 3
        by_id = {run["id"]: run for run in runs}
        for candidate in outcome["candidates"]:
            run = by_id[candidate["run_id"]]
            assert run["strategy_id"] == candidate["strategy_id"]
            assert run["label"] == RUN_LABEL_PREFIX + candidate["name"]
            assert run["stats"] == candidate["stats"]
            assert run["source"] == "sample"  # D1 history badge

    async def test_stored_assistant_actions_carry_research(self, us_chat):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": US_RESEARCH_MESSAGE}
        )
        body = resp.json()
        conn = get_conn(us_chat.db_file)
        try:
            actions = json.loads(
                conn.execute(
                    "SELECT actions FROM chat_messages WHERE role = 'assistant'"
                ).fetchone()["actions"]
            )
        finally:
            conn.close()
        assert actions["research"] == body["research"]

    async def test_research_key_absent_from_other_mock_branches(self, us_chat):
        for message in ("hello there", "backtest a dip buy on NVDA"):
            resp = await us_chat.client.post("/api/chat/", json={"message": message})
            body = resp.json()
            assert "research" not in body
            conn = get_conn(us_chat.db_file)
            try:
                row = conn.execute(
                    "SELECT actions FROM chat_messages WHERE role = 'assistant' "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            assert "research" not in json.loads(row["actions"])


# ---------------------------------------------------------------------------
# LLM_MOCK research branch — CN (zh)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMockResearchBranchZH:
    async def test_zh_research_message_runs_on_600519(self, cn_chat):
        resp = await cn_chat.client.post(
            "/api/chat/", json={"message": ZH_RESEARCH_MESSAGE}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "strategies" not in body  # '研究' beat '策略'
        outcome = body["research"][0]
        _assert_ranked_batch(outcome, "600519")
        names = [c["name"] for c in outcome["candidates"]]
        assert names == ["均线金叉", "RSI 超跌反弹", "动量突破"]

        rows = _db_strategies(cn_chat.db_file)
        assert len(rows) == 3
        assert all(row["status"] == "draft" for row in rows)
        assert {row["template"] for row in rows} == set(MOCK_TEMPLATES)

        runs = (await cn_chat.client.get("/api/backtest/runs")).json()["runs"]
        assert {run["strategy_id"] for run in runs} == {
            c["strategy_id"] for c in outcome["candidates"]
        }

    async def test_zh_mock_candidates_cannot_afford_a_maotai_lot(self, cn_chat):
        # Documented CN outcome on the committed sample series: 600519 trades
        # around ¥1700, so the 20% cash_pct budget (~¥20k of the ¥100k seed)
        # cannot buy ONE 100-share lot — every fired entry is rejected, all
        # three candidates finish with zero round trips, and the null
        # recommendation path (contract §2.2) is exercised for real.
        resp = await cn_chat.client.post(
            "/api/chat/", json={"message": ZH_RESEARCH_MESSAGE}
        )
        outcome = resp.json()["research"][0]
        assert all(c["traded"] is False for c in outcome["candidates"])
        assert outcome["recommended_strategy_id"] is None


# ---------------------------------------------------------------------------
# Faked-LLM turns: isolation + transaction boundary
# ---------------------------------------------------------------------------


def _fake_completion_factory(payload: dict):
    def fake_completion(*args, **kwargs):
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


async def _turn_with_research(ctx, monkeypatch, research: list[dict]) -> dict:
    """Drive one chat turn through a faked LLM returning `research`."""
    import litellm

    monkeypatch.setenv("LLM_MOCK", "false")
    payload = {
        "message": "done",
        "trades": [],
        "watchlist_changes": [],
        "research": research,
    }
    monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))
    resp = await ctx.client.post("/api/chat/", json={"message": "research AAPL"})
    assert resp.status_code == 200, resp.text
    return resp.json()


CANDIDATE = {"name": "Tpl", "template": "dip_buyer"}


@pytest.mark.asyncio
class TestChatResearchActions:
    async def test_instructions_fail_independently(self, us_chat, monkeypatch):
        body = await _turn_with_research(
            us_chat,
            monkeypatch,
            [
                {"ticker": "AAPL", "candidates": [dict(CANDIDATE)]},  # guard: 1
                {"ticker": "AAPL",
                 "candidates": [dict(CANDIDATE), {**CANDIDATE, "name": "Tpl 2"}]},
            ],
        )
        first, second = body["research"]
        assert first["status"] == "failed"
        assert "2-4 candidates" in first["error"]
        assert second["status"] == "completed"
        assert len(_db_strategies(us_chat.db_file)) == 2

    async def test_no_research_key_when_turn_has_none(self, us_chat, monkeypatch):
        body = await _turn_with_research(us_chat, monkeypatch, [])
        assert "research" not in body

    async def test_failed_batch_persists_nothing(self, us_chat, monkeypatch):
        body = await _turn_with_research(
            us_chat,
            monkeypatch,
            [{"ticker": "ZZZZ",
              "candidates": [dict(CANDIDATE), {**CANDIDATE, "name": "Tpl 2"}]}],
        )
        assert body["research"][0]["status"] == "failed"
        assert _db_strategies(us_chat.db_file) == []
        runs = (await us_chat.client.get("/api/backtest/runs")).json()["runs"]
        assert runs == []


# ---------------------------------------------------------------------------
# Schema + prompts
# ---------------------------------------------------------------------------


class TestSchemaAndPrompts:
    def test_research_schema_extends_the_turn_schema(self):
        # D4 §2.1: the research array rides a SUBCLASS — the pinned
        # ChatTurnResponse field set gains exactly `research` here and the
        # frozen ChatResponse is untouched (its own tests pin that).
        assert set(ChatResearchTurnResponse.model_fields) == set(
            ChatTurnResponse.model_fields
        ) | {"research"}
        # Defaults keep a plain payload parseable (LLM omitting the array).
        parsed = ChatResearchTurnResponse.model_validate_json('{"message": "hi"}')
        assert parsed.research == []
        parsed = ChatResearchTurnResponse.model_validate_json(
            '{"message": "hi", "research": [{"ticker": "AAPL"}]}'
        )
        assert parsed.research[0].days is None
        assert parsed.research[0].candidates == []

    @pytest.mark.parametrize("prompt", [SYSTEM_PROMPT, SYSTEM_PROMPT_ZH])
    def test_prompts_advertise_research(self, prompt):
        assert "'research'" in prompt
        for token in ("hypothesis", "candidates", "2-4"):
            assert token in prompt

    def test_prompts_document_the_score_in_each_language(self):
        assert "robustness score" in SYSTEM_PROMPT
        assert "稳健分" in SYSTEM_PROMPT_ZH
