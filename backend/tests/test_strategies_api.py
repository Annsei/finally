"""Strategy CRUD + state machine + performance + templates API tests (P2 §6/§10).

Covers:
- the six-template static registry (keys, configs, whitelist validity)
- POST create (draft) + the full-validation 400 matrix
- GET list (default hides archived; status filter; invalid status 400)
- PATCH state machine: every legal transition, illegal moves 400, deploy
  without an exit 400, archived terminal, archive clears open state
- PATCH config edit: live edit 400 "pause first", validation 400s, edits land
- DELETE: live 400, drafts delete, unknown 404
- cross-user isolation: another user's strategies 404 through every endpoint
- GET /{id}/performance: analytics-parity stats (win_rate 4dp, profit_factor,
  max drawdown source), the 0-baseline realized-P&L curve, and the open-
  position mark-to-market end point
"""

from __future__ import annotations

import json
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.indicators import validate_condition_group, validate_exits, validate_sizing
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.strategies import STRATEGY_TEMPLATES, create_strategies_router

VALID_BODY = {
    "name": "Test strategy",
    "ticker": "NVDA",
    "entry": {"all": [{"field": "price", "op": "above", "value": 9_999_999}]},
    "exits": {"stop_loss_pct": 5},
    "sizing": {"mode": "fixed_qty", "qty": 1},
}


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch):
    """Strategies router + auth (for cross-user clients) on a temp DB."""
    db_file = str(tmp_path / "strategies.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    from app.routes.auth import create_auth_router

    test_app = FastAPI()
    test_app.include_router(create_strategies_router(price_cache, db_file))
    test_app.include_router(create_auth_router(db_file))

    async with AsyncExitStack() as stack:

        async def make_client() -> AsyncClient:
            return await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
            )

        client = await make_client()
        yield SimpleNamespace(
            client=client,
            db_file=db_file,
            price_cache=price_cache,
            make_client=make_client,
        )


async def _create(client, **overrides) -> dict:
    resp = await client.post("/api/strategies", json={**VALID_BODY, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()["strategy"]


def _insert_trade(
    db_file: str,
    strategy_id: str | None,
    *,
    side: str,
    quantity: float = 1.0,
    price: float = 100.0,
    realized_pnl: float | None = None,
    executed_at: str | None = None,
    user_id: str = "default",
) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, "
            "commission, realized_pnl, strategy_id, executed_at) "
            "VALUES (?, ?, 'NVDA', ?, ?, ?, 0, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                user_id,
                side,
                quantity,
                price,
                realized_pnl,
                strategy_id,
                executed_at or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTemplates:
    async def test_registry_serves_the_six_fixed_templates(self, api):
        resp = await api.client.get("/api/strategies/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert [t["key"] for t in templates] == [
            "dip_buyer",
            "momentum_breakout",
            "ma_golden_cross",
            "grid_lite",
            "rsi_rebound",
            "trend_rider",
        ]
        for t in templates:
            assert t["ticker_hint"] is None
            assert set(t) == {"key", "ticker_hint", "entry", "exits", "sizing"}

    async def test_contract_pinned_configs(self, api):
        resp = await api.client.get("/api/strategies/templates")
        by_key = {t["key"]: t for t in resp.json()["templates"]}
        dip = by_key["dip_buyer"]
        assert dip["entry"] == {
            "all": [{"field": "day_change_pct", "op": "below", "value": -3}]
        }
        assert dip["exits"] == {"take_profit_pct": 4, "stop_loss_pct": 3}
        assert dip["sizing"] == {"mode": "cash_pct", "pct": 20}
        trend = by_key["trend_rider"]
        assert trend["entry"] == {
            "all": [
                {"field": "ma", "op": "above", "value": 0, "params": {"period": 30}},
                {"field": "day_change_pct", "op": "above", "value": 0.5},
            ]
        }
        assert trend["exits"] == {"trailing_stop_pct": 3}
        assert trend["sizing"] == {"mode": "cash_pct", "pct": 25}
        assert by_key["ma_golden_cross"]["entry"]["all"][0]["params"] == {
            "fast": 5,
            "slow": 20,
        }


class TestTemplateValidity:
    def test_every_template_passes_the_whitelist_validators(self):
        for t in STRATEGY_TEMPLATES:
            assert validate_condition_group(t["entry"]) is None, t["key"]
            assert validate_exits(t["exits"]) is None, t["key"]
            assert validate_sizing(t["sizing"]) is None, t["key"]


# ---------------------------------------------------------------------------
# Create + list + get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateAndList:
    async def test_create_returns_draft_and_lists_it(self, api):
        created = await _create(api.client, template="dip_buyer")
        assert created["status"] == "draft"
        assert created["template"] == "dip_buyer"
        assert created["runs_count"] == 0
        assert created["realized_pnl"] == 0.0
        assert created["open_qty"] == 0

        resp = await api.client.get("/api/strategies")
        strategies = resp.json()["strategies"]
        assert [s["id"] for s in strategies] == [created["id"]]
        assert strategies[0]["entry"] == VALID_BODY["entry"]
        assert strategies[0]["sizing"] == {"mode": "fixed_qty", "qty": 1.0}

    @pytest.mark.parametrize(
        "overrides, fragment",
        [
            ({"name": ""}, "name"),
            ({"name": "x" * 41}, "name"),
            ({"ticker": "ZZZZ"}, "Ticker not found"),
            (
                {"entry": {"all": [{"field": "nope", "op": "above", "value": 1}]}},
                "entry",
            ),
            ({"entry": {"all": [], "any": []}}, "entry"),
            ({"exits": {"stop_loss_pct": -1}}, "stop_loss_pct"),
            ({"exits": {"bogus": 1}}, "exits"),
            ({"sizing": {"mode": "fixed_qty", "qty": 0}}, "sizing"),
            ({"sizing": {"mode": "cash_pct", "pct": 200}}, "sizing"),
        ],
    )
    async def test_create_validation_matrix_400(self, api, overrides, fragment):
        resp = await api.client.post("/api/strategies", json={**VALID_BODY, **overrides})
        assert resp.status_code == 400
        assert fragment.lower() in resp.json()["error"].lower()

    async def test_default_list_hides_archived_status_all_includes(self, api):
        kept = await _create(api.client, name="kept")
        gone = await _create(api.client, name="gone")
        resp = await api.client.patch(
            f"/api/strategies/{gone['id']}", json={"status": "archived"}
        )
        assert resp.status_code == 200

        default_ids = {
            s["id"] for s in (await api.client.get("/api/strategies")).json()["strategies"]
        }
        assert default_ids == {kept["id"]}

        all_ids = {
            s["id"]
            for s in (await api.client.get("/api/strategies?status=all")).json()[
                "strategies"
            ]
        }
        assert all_ids == {kept["id"], gone["id"]}

        archived = (
            await api.client.get("/api/strategies?status=archived")
        ).json()["strategies"]
        assert [s["id"] for s in archived] == [gone["id"]]

    async def test_list_invalid_status_400(self, api):
        resp = await api.client.get("/api/strategies?status=bogus")
        assert resp.status_code == 400

    async def test_get_single_and_404(self, api):
        created = await _create(api.client)
        resp = await api.client.get(f"/api/strategies/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["strategy"]["id"] == created["id"]
        assert (await api.client.get("/api/strategies/nope")).status_code == 404


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStateMachine:
    async def test_draft_deploys_live_and_stamps_deployed_at(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}", json={"status": "live"}
        )
        assert resp.status_code == 200
        strategy = resp.json()["strategy"]
        assert strategy["status"] == "live"
        assert strategy["deployed_at"] is not None

    async def test_deploy_without_any_exit_400(self, api):
        created = await _create(api.client, exits={})
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}", json={"status": "live"}
        )
        assert resp.status_code == 400
        assert "exit" in resp.json()["error"].lower()

    async def test_live_pauses_and_resumes(self, api):
        created = await _create(api.client)
        sid = created["id"]
        await api.client.patch(f"/api/strategies/{sid}", json={"status": "live"})
        resp = await api.client.patch(f"/api/strategies/{sid}", json={"status": "paused"})
        assert resp.status_code == 200
        assert resp.json()["strategy"]["status"] == "paused"
        resp = await api.client.patch(f"/api/strategies/{sid}", json={"status": "live"})
        assert resp.status_code == 200
        assert resp.json()["strategy"]["status"] == "live"

    async def test_draft_cannot_pause(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}", json={"status": "paused"}
        )
        assert resp.status_code == 400

    async def test_archive_clears_open_state(self, api):
        created = await _create(api.client)
        sid = created["id"]
        conn = get_conn(api.db_file)
        conn.execute(
            "UPDATE strategies SET status = 'live', open_qty = 5, open_price = 100, "
            "opened_at = '2026-01-01T00:00:00+00:00', high_water = 120, "
            "cooldown_until = 1 WHERE id = ?",
            (sid,),
        )
        conn.commit()
        conn.close()

        resp = await api.client.patch(f"/api/strategies/{sid}", json={"status": "archived"})
        assert resp.status_code == 200
        strategy = resp.json()["strategy"]
        assert strategy["status"] == "archived"
        assert strategy["open_qty"] == 0
        assert strategy["open_price"] is None
        assert strategy["opened_at"] is None

    async def test_archived_is_terminal(self, api):
        created = await _create(api.client)
        sid = created["id"]
        await api.client.patch(f"/api/strategies/{sid}", json={"status": "archived"})
        for target in ("live", "paused", "draft", "archived"):
            resp = await api.client.patch(
                f"/api/strategies/{sid}", json={"status": target}
            )
            assert resp.status_code == 400, target

    async def test_unknown_status_value_400(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}", json={"status": "warp"}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Config edits + delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEditAndDelete:
    async def test_draft_config_edit_lands(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}",
            json={"name": "Renamed", "exits": {"take_profit_pct": 9}},
        )
        assert resp.status_code == 200
        strategy = resp.json()["strategy"]
        assert strategy["name"] == "Renamed"
        assert strategy["exits"] == {"take_profit_pct": 9}
        assert strategy["entry"] == VALID_BODY["entry"]  # untouched

    async def test_live_edit_400_pause_first(self, api):
        created = await _create(api.client)
        sid = created["id"]
        await api.client.patch(f"/api/strategies/{sid}", json={"status": "live"})
        resp = await api.client.patch(f"/api/strategies/{sid}", json={"name": "nope"})
        assert resp.status_code == 400
        assert "pause first" in resp.json()["error"].lower()

    async def test_edit_validation_400(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(
            f"/api/strategies/{created['id']}",
            json={"entry": {"any": [{"field": "price", "op": "sideways", "value": 1}]}},
        )
        assert resp.status_code == 400

    async def test_empty_patch_body_400(self, api):
        created = await _create(api.client)
        resp = await api.client.patch(f"/api/strategies/{created['id']}", json={})
        assert resp.status_code == 400

    async def test_delete_draft_ok_live_400_unknown_404(self, api):
        created = await _create(api.client)
        sid = created["id"]
        await api.client.patch(f"/api/strategies/{sid}", json={"status": "live"})
        assert (await api.client.delete(f"/api/strategies/{sid}")).status_code == 400
        await api.client.patch(f"/api/strategies/{sid}", json={"status": "paused"})
        resp = await api.client.delete(f"/api/strategies/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert (await api.client.get(f"/api/strategies/{sid}")).status_code == 404
        assert (await api.client.delete(f"/api/strategies/{sid}")).status_code == 404

    async def test_delete_keeps_trade_attribution(self, api):
        created = await _create(api.client)
        sid = created["id"]
        _insert_trade(api.db_file, sid, side="sell", realized_pnl=10.0)
        await api.client.delete(f"/api/strategies/{sid}")
        conn = get_conn(api.db_file)
        row = conn.execute(
            "SELECT strategy_id FROM trades WHERE strategy_id = ?", (sid,)
        ).fetchone()
        conn.close()
        assert row is not None


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCrossUser:
    async def test_foreign_strategies_are_404_everywhere(self, api):
        created = await _create(api.client)  # anonymous 'default' user
        sid = created["id"]

        mallory = await api.make_client()
        resp = await mallory.post("/api/auth/login", json={"name": "Mallory"})
        assert resp.status_code == 200

        assert (await mallory.get(f"/api/strategies/{sid}")).status_code == 404
        assert (
            await mallory.patch(f"/api/strategies/{sid}", json={"status": "live"})
        ).status_code == 404
        assert (await mallory.delete(f"/api/strategies/{sid}")).status_code == 404
        assert (
            await mallory.get(f"/api/strategies/{sid}/performance")
        ).status_code == 404
        listed = (await mallory.get("/api/strategies")).json()["strategies"]
        assert listed == []


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformance:
    async def test_stats_and_zero_baseline_curve(self, api):
        created = await _create(api.client)
        sid = created["id"]
        # Two round trips: +30 win, -10 loss; plus a stray manual trade that
        # must NOT count (no strategy_id).
        _insert_trade(api.db_file, sid, side="buy", executed_at="2026-01-01T10:00:00+00:00")
        _insert_trade(
            api.db_file, sid, side="sell", realized_pnl=30.0,
            executed_at="2026-01-02T10:00:00+00:00",
        )
        _insert_trade(api.db_file, sid, side="buy", executed_at="2026-01-03T10:00:00+00:00")
        _insert_trade(
            api.db_file, sid, side="sell", realized_pnl=-10.0,
            executed_at="2026-01-04T10:00:00+00:00",
        )
        _insert_trade(api.db_file, None, side="sell", realized_pnl=999.0)

        resp = await api.client.get(f"/api/strategies/{sid}/performance")
        assert resp.status_code == 200
        payload = resp.json()
        stats = payload["stats"]
        assert stats["realized_pnl"] == 20.0
        assert stats["round_trips"] == 2
        assert stats["win_rate"] == 0.5  # analytics 4dp convention
        assert stats["profit_factor"] == 3.0  # 30 / 10
        assert stats["fires"] == 2
        # Curve: cumulative realized P&L at each sell — 0-baseline.
        assert [p["value"] for p in payload["equity_curve"]] == [30.0, 20.0]
        assert payload["equity_curve"][0]["time"] < payload["equity_curve"][1]["time"]
        # Drawdown from the shared analytics helper: peak 30 -> 20 = 33.3333%.
        assert stats["max_drawdown_pct"] == pytest.approx(33.3333, abs=1e-4)
        # Trades: only this strategy's fills, ascending.
        assert [t["side"] for t in payload["trades"]] == ["buy", "sell", "buy", "sell"]

    async def test_open_position_adds_mark_to_market_end_point(self, api):
        created = await _create(api.client)
        sid = created["id"]
        _insert_trade(
            api.db_file, sid, side="sell", realized_pnl=10.0,
            executed_at="2026-01-01T10:00:00+00:00",
        )
        conn = get_conn(api.db_file)
        conn.execute(
            "UPDATE strategies SET open_qty = 2, open_price = 100 WHERE id = ?",
            (sid,),
        )
        conn.commit()
        conn.close()
        api.price_cache.update("NVDA", 110.0)

        payload = (await api.client.get(f"/api/strategies/{sid}/performance")).json()
        values = [p["value"] for p in payload["equity_curve"]]
        # 10 realized + 2 * (110 - 100) unrealized = 30 at the live mark.
        assert values == [10.0, 30.0]

    async def test_empty_strategy_zeroed_stats(self, api):
        created = await _create(api.client)
        payload = (
            await api.client.get(f"/api/strategies/{created['id']}/performance")
        ).json()
        assert payload["stats"] == {
            "realized_pnl": 0.0,
            "round_trips": 0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
            "fires": 0,
        }
        assert payload["equity_curve"] == []
        assert payload["trades"] == []

    async def test_list_aggregates_runs_count_and_realized_pnl(self, api):
        created = await _create(api.client)
        sid = created["id"]
        _insert_trade(api.db_file, sid, side="sell", realized_pnl=12.5)
        conn = get_conn(api.db_file)
        conn.execute(
            "INSERT INTO backtest_runs (id, user_id, strategy_id, label, "
            "created_at, config, stats, equity_curve, baseline_curve, trades) "
            "VALUES (?, 'default', ?, NULL, ?, ?, '{}', '[]', '[]', '[]')",
            (
                str(uuid.uuid4()),
                sid,
                datetime.now(timezone.utc).isoformat(),
                json.dumps({"ticker": "NVDA"}),
            ),
        )
        conn.commit()
        conn.close()

        listed = (await api.client.get("/api/strategies")).json()["strategies"]
        assert listed[0]["runs_count"] == 1
        assert listed[0]["realized_pnl"] == 12.5
