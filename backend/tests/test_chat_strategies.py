"""Chat `strategies` action tests (P2 §7/§10).

Covers:
- Step 6e executes the four actions: create (template-first, explicit
  entry/exits/sizing override), backtest (engine run persisted to the Run
  Library with strategy_id; compact stats + run_id in the outcome), deploy
  and pause (the §6 state machine; deploy without an exit → failed outcome)
- strategy resolution by id and by case-insensitive name (newest wins)
- per-item failures never abort the batch
- the `strategies` response/actions key appears ONLY when the turn contained
  strategy actions (the existing non-empty-only rule)
- the deterministic LLM_MOCK 'strategy' branch: create ma_golden_cross NVDA
  + a persisted seed-4242 20-day backtest; a message containing both
  'strategy' and 'backtest' keeps routing to the M5 backtest branch; the zh
  variant uses the first CN universe ticker with byte-identical action
  structure
- the system prompts advertise the strategies array + all six template keys
  in both languages
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.profiles import CN_PROFILE
from app.market.seed_prices import SEED_PRICES
from app.routes.backtest_runs import create_backtest_runs_router
from app.routes.chat import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_ZH,
    ChatTurnResponse,
    create_chat_router,
)
from app.routes.strategies import create_strategies_router

TEMPLATE_KEYS = (
    "dip_buyer",
    "momentum_breakout",
    "ma_golden_cross",
    "grid_lite",
    "rsi_rebound",
    "trend_rider",
)


def _fake_completion_factory(payload: dict):
    def fake_completion(*args, **kwargs):
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return fake_completion


async def _build_app(tmp_path, monkeypatch, *, profile=None, llm_mock="true"):
    db_file = str(tmp_path / "chat_strategies.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", llm_mock)
    if profile is None:
        init_db(db_file)
    else:
        init_db(
            db_file,
            seed_cash=profile.seed_cash,
            default_watchlist=list(profile.universe.default_watchlist),
        )

    price_cache = PriceCache()
    seeds = SEED_PRICES if profile is None else profile.universe.seed_prices
    for ticker, price in seeds.items():
        price_cache.update(ticker, price)
    if profile is not None:
        # The zh mock strategy branch backtests the first CN universe ticker,
        # which is already seeded above; NVDA stays unknown on purpose.
        pass

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


async def _turn_with_actions(ctx, monkeypatch, strategies: list[dict]) -> dict:
    """Drive one chat turn through a faked LLM returning `strategies`."""
    import litellm

    monkeypatch.setenv("LLM_MOCK", "false")
    payload = {
        "message": "done",
        "trades": [],
        "watchlist_changes": [],
        "strategies": strategies,
    }
    monkeypatch.setattr(litellm, "completion", _fake_completion_factory(payload))
    resp = await ctx.client.post("/api/chat/", json={"message": "manage my strategies"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _db_strategies(db_file: str) -> list:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT * FROM strategies ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 6e — the four actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatStrategyActions:
    async def test_create_from_template(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat,
            monkeypatch,
            [{"action": "create", "name": "Dips", "ticker": "NVDA",
              "template": "dip_buyer"}],
        )
        outcome = body["strategies"][0]
        assert outcome["status"] == "created"
        assert outcome["action"] == "create"
        assert outcome["ticker"] == "NVDA"
        rows = _db_strategies(us_chat.db_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "draft"
        assert rows[0]["template"] == "dip_buyer"
        assert json.loads(rows[0]["entry"]) == {
            "all": [{"field": "day_change_pct", "op": "below", "value": -3}]
        }
        # The stored assistant chat row carries the outcome too.
        conn = get_conn(us_chat.db_file)
        actions = json.loads(
            conn.execute(
                "SELECT actions FROM chat_messages WHERE role = 'assistant'"
            ).fetchone()["actions"]
        )
        conn.close()
        assert actions["strategies"][0]["status"] == "created"

    async def test_create_explicit_fields_override_template(self, us_chat, monkeypatch):
        explicit_entry = {"all": [{"field": "price", "op": "below", "value": 100}]}
        body = await _turn_with_actions(
            us_chat,
            monkeypatch,
            [{"action": "create", "name": "Override", "ticker": "AAPL",
              "template": "dip_buyer", "entry": explicit_entry,
              "sizing": {"mode": "fixed_qty", "qty": 2}}],
        )
        assert body["strategies"][0]["status"] == "created"
        row = _db_strategies(us_chat.db_file)[0]
        assert json.loads(row["entry"]) == explicit_entry  # explicit wins
        assert json.loads(row["sizing"]) == {"mode": "fixed_qty", "qty": 2.0}
        # Template still supplies what was not overridden.
        assert json.loads(row["exits"]) == {"take_profit_pct": 4, "stop_loss_pct": 3}

    async def test_create_failures_are_outcomes_not_aborts(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat,
            monkeypatch,
            [
                {"action": "create", "name": "Bad", "ticker": "ZZZZ",
                 "template": "dip_buyer"},
                {"action": "create", "name": "Bad2", "ticker": "NVDA",
                 "template": "not_a_template"},
                {"action": "create", "name": "Good", "ticker": "NVDA",
                 "template": "dip_buyer"},
            ],
        )
        statuses = [o["status"] for o in body["strategies"]]
        assert statuses == ["failed", "failed", "created"]
        assert "Ticker not found" in body["strategies"][0]["error"]
        assert "template" in body["strategies"][1]["error"].lower()
        assert len(_db_strategies(us_chat.db_file)) == 1

    async def test_backtest_by_name_persists_run(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat,
            monkeypatch,
            [
                {"action": "create", "name": "BT Target", "ticker": "NVDA",
                 "template": "dip_buyer"},
                {"action": "backtest", "strategy": "bt target", "days": 10,
                 "seed": 99},
            ],
        )
        create_outcome, bt_outcome = body["strategies"]
        assert create_outcome["status"] == "created"
        assert bt_outcome["status"] == "completed"
        assert bt_outcome["action"] == "backtest"
        assert bt_outcome["strategy_id"] == create_outcome["strategy_id"]
        assert "run_id" in bt_outcome
        assert "total_return_pct" in bt_outcome["stats"]
        assert "equity_curve" not in bt_outcome  # compact — never curves

        # The run really landed in the library, attributed to the strategy.
        resp = await us_chat.client.get(
            f"/api/backtest/runs/{bt_outcome['run_id']}"
        )
        assert resp.status_code == 200
        run = resp.json()["run"]
        assert run["strategy_id"] == create_outcome["strategy_id"]
        assert run["config"]["seed"] == 99
        assert run["config"]["days"] == 10

    async def test_backtest_by_id(self, us_chat, monkeypatch):
        await _turn_with_actions(
            us_chat, monkeypatch,
            [{"action": "create", "name": "ById", "ticker": "NVDA",
              "template": "dip_buyer"}],
        )
        sid = _db_strategies(us_chat.db_file)[0]["id"]
        body = await _turn_with_actions(
            us_chat, monkeypatch,
            [{"action": "backtest", "strategy": sid, "days": 10, "seed": 5}],
        )
        assert body["strategies"][0]["status"] == "completed"
        assert body["strategies"][0]["strategy_id"] == sid

    async def test_backtest_unknown_strategy_failed(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat, monkeypatch,
            [{"action": "backtest", "strategy": "ghost"}],
        )
        outcome = body["strategies"][0]
        assert outcome["status"] == "failed"
        assert outcome["error"] == "Strategy not found"

    async def test_deploy_and_pause_run_the_state_machine(self, us_chat, monkeypatch):
        await _turn_with_actions(
            us_chat, monkeypatch,
            [{"action": "create", "name": "Lifecycle", "ticker": "NVDA",
              "template": "dip_buyer"}],
        )
        body = await _turn_with_actions(
            us_chat, monkeypatch, [{"action": "deploy", "strategy": "Lifecycle"}]
        )
        assert body["strategies"][0]["status"] == "deployed"
        assert _db_strategies(us_chat.db_file)[0]["status"] == "live"

        body = await _turn_with_actions(
            us_chat, monkeypatch, [{"action": "pause", "strategy": "Lifecycle"}]
        )
        assert body["strategies"][0]["status"] == "paused"
        assert _db_strategies(us_chat.db_file)[0]["status"] == "paused"

    async def test_deploy_without_exit_fails(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat,
            monkeypatch,
            [
                {"action": "create", "name": "NoExit", "ticker": "NVDA",
                 "entry": {"all": [{"field": "price", "op": "above", "value": 1}]},
                 "exits": {}, "sizing": {"mode": "fixed_qty", "qty": 1}},
                {"action": "deploy", "strategy": "NoExit"},
            ],
        )
        deploy = body["strategies"][1]
        assert deploy["status"] == "failed"
        assert "exit" in deploy["error"].lower()
        assert _db_strategies(us_chat.db_file)[0]["status"] == "draft"

    async def test_unknown_action_failed(self, us_chat, monkeypatch):
        body = await _turn_with_actions(
            us_chat, monkeypatch, [{"action": "explode", "strategy": "x"}]
        )
        assert body["strategies"][0]["status"] == "failed"

    async def test_no_strategies_key_when_turn_has_none(self, us_chat, monkeypatch):
        body = await _turn_with_actions(us_chat, monkeypatch, [])
        assert "strategies" not in body


# ---------------------------------------------------------------------------
# LLM_MOCK strategy branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMockStrategyBranch:
    async def test_strategy_keyword_creates_draft_and_persists_run(self, us_chat):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": "Set up a strategy for NVDA and test it"}
        )
        assert resp.status_code == 200
        body = resp.json()
        outcomes = body["strategies"]
        assert [o["status"] for o in outcomes] == ["created", "completed"]
        assert outcomes[0]["ticker"] == "NVDA"
        assert outcomes[0]["name"] == "MA Golden Cross"
        assert outcomes[1]["run_id"]

        rows = _db_strategies(us_chat.db_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "draft"  # chat create never deploys
        assert rows[0]["template"] == "ma_golden_cross"

        run = (
            await us_chat.client.get(f"/api/backtest/runs/{outcomes[1]['run_id']}")
        ).json()["run"]
        assert run["strategy_id"] == rows[0]["id"]
        assert run["config"]["seed"] == 4242  # deterministic mock seed
        assert run["config"]["days"] == 20

    async def test_zh_variant_uses_first_cn_universe_ticker(self, cn_chat):
        resp = await cn_chat.client.post("/api/chat/", json={"message": "帮我建一个策略"})
        assert resp.status_code == 200
        outcomes = resp.json()["strategies"]
        first_cn = CN_PROFILE.universe.default_watchlist[0]
        assert [o["status"] for o in outcomes] == ["created", "completed"]
        assert outcomes[0]["ticker"] == first_cn
        # Byte-identical action structure to the US branch, ticker aside.
        assert outcomes[0]["name"] == "MA Golden Cross"
        run = (
            await cn_chat.client.get(f"/api/backtest/runs/{outcomes[1]['run_id']}")
        ).json()["run"]
        assert run["config"]["seed"] == 4242
        assert run["config"]["days"] == 20

    async def test_strategy_plus_backtest_keyword_routes_to_backtest_branch(
        self, us_chat
    ):
        resp = await us_chat.client.post(
            "/api/chat/", json={"message": "backtest a strategy on NVDA"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "strategies" not in body  # the M5 branch won
        assert body["backtests"][0]["status"] == "completed"
        assert _db_strategies(us_chat.db_file) == []

    async def test_default_branch_untouched_by_the_new_branch(self, us_chat):
        resp = await us_chat.client.post("/api/chat/", json={"message": "hello"})
        body = resp.json()
        assert "strategies" not in body
        assert body["watchlist_changes"][0]["ticker"] == "PYPL"


# ---------------------------------------------------------------------------
# Schema + prompts
# ---------------------------------------------------------------------------


class TestSchemaAndPrompts:
    def test_turn_schema_adds_only_strategies(self):
        assert set(ChatTurnResponse.model_fields) == {
            "message",
            "trades",
            "watchlist_changes",
            "orders",
            "rules",
            "backtests",
            "strategies",
        }
        # Defaults keep a plain payload parseable (LLM omitting the array).
        parsed = ChatTurnResponse.model_validate_json('{"message": "hi"}')
        assert parsed.strategies == []

    @pytest.mark.parametrize("prompt", [SYSTEM_PROMPT, SYSTEM_PROMPT_ZH])
    def test_prompts_advertise_strategies_and_templates(self, prompt):
        assert "'strategies'" in prompt
        for key in TEMPLATE_KEYS:
            assert key in prompt
        for token in ("'create'", "'backtest'", "'deploy'", "'pause'",
                      "trailing_stop_pct", "max_holding_days", "cash_pct"):
            assert token in prompt
