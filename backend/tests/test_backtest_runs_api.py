"""Run Library API tests (P2 §5/§10).

Covers:
- POST /api/backtest/runs in both body shapes: {strategy_id, ...} (config
  built from the strategy row; foreign/unknown → 404) and the legacy
  Backtest-tab field set (server-side re-run — same config+seed produces the
  exact stats the stateless POST /api/backtest reports, so saved numbers
  cannot be forged)
- same-seed determinism of persisted runs
- validation 400s (missing legacy fields, bad days, bad strategy config)
- trades truncation to 200 entries at write time
- GET list: newest first, stats only (no curves), strategy_id/ticker
  filters, limit default/clamp/400
- GET {id} full payload, DELETE, and cross-user 404s
- strategy-level cascade query (?strategy_id=) powering the detail page
"""

from __future__ import annotations

import uuid
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.backtest_runs import (
    MAX_STORED_TRADES,
    create_backtest_runs_router,
    insert_backtest_run_on_conn,
)

LEGACY_BODY = {
    "ticker": "NVDA",
    "trigger_type": "day_change_pct_below",
    "threshold": -2,
    "quantity": 5,
    "take_profit_pct": 5,
    "stop_loss_pct": 3,
    "days": 10,
    "runs": 1,
    "seed": 1234,
}

STRATEGY_BODY = {
    "name": "Runs test",
    "ticker": "NVDA",
    "entry": {"all": [{"field": "day_change_pct", "op": "below", "value": -2}]},
    "exits": {"take_profit_pct": 5, "stop_loss_pct": 3},
    "sizing": {"mode": "fixed_qty", "qty": 5},
}


@pytest_asyncio.fixture
async def api(tmp_path, monkeypatch):
    """Runs + strategies + stateless backtest + auth routers on a temp DB."""
    db_file = str(tmp_path / "runs.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    from app.routes.auth import create_auth_router
    from app.routes.backtest import create_backtest_router
    from app.routes.strategies import create_strategies_router

    test_app = FastAPI()
    test_app.include_router(create_backtest_router(price_cache))
    test_app.include_router(create_backtest_runs_router(price_cache, db_file))
    test_app.include_router(create_strategies_router(price_cache, db_file))
    test_app.include_router(create_auth_router(db_file))

    async with AsyncExitStack() as stack:

        async def make_client() -> AsyncClient:
            return await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
            )

        client = await make_client()
        yield SimpleNamespace(
            client=client, db_file=db_file, make_client=make_client
        )


async def _create_strategy(client, **overrides) -> str:
    resp = await client.post("/api/strategies", json={**STRATEGY_BODY, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()["strategy"]["id"]


@pytest.mark.asyncio
class TestSaveRun:
    async def test_strategy_shape_persists_a_run(self, api):
        sid = await _create_strategy(api.client)
        resp = await api.client.post(
            "/api/backtest/runs",
            json={"strategy_id": sid, "days": 10, "seed": 777, "label": "first"},
        )
        assert resp.status_code == 201, resp.text
        run = resp.json()["run"]
        assert run["strategy_id"] == sid
        assert run["label"] == "first"
        assert run["config"]["source"] == "strategy"
        assert run["config"]["seed"] == 777
        assert run["config"]["days"] == 10
        assert run["config"]["entry"] == STRATEGY_BODY["entry"]
        assert len(run["equity_curve"]) <= 400
        assert len(run["baseline_curve"]) <= 400
        assert set(run["stats"]) >= {
            "total_return_pct",
            "buy_hold_return_pct",
            "max_drawdown_pct",
            "final_equity",
            "fires",
            "round_trips",
            "win_rate",
        }
        # The strategy's runs_count reflects the persisted run.
        strategy = (await api.client.get(f"/api/strategies/{sid}")).json()["strategy"]
        assert strategy["runs_count"] == 1

    async def test_strategy_shape_same_seed_is_deterministic(self, api):
        sid = await _create_strategy(api.client)
        body = {"strategy_id": sid, "days": 10, "seed": 4242}
        first = (await api.client.post("/api/backtest/runs", json=body)).json()["run"]
        second = (await api.client.post("/api/backtest/runs", json=body)).json()["run"]
        assert first["stats"] == second["stats"]
        assert [p["value"] for p in first["equity_curve"]] == [
            p["value"] for p in second["equity_curve"]
        ]

    async def test_strategy_shape_unknown_or_foreign_404(self, api):
        resp = await api.client.post(
            "/api/backtest/runs", json={"strategy_id": "nope"}
        )
        assert resp.status_code == 404

        sid = await _create_strategy(api.client)
        mallory = await api.make_client()
        await mallory.post("/api/auth/login", json={"name": "Mallory"})
        resp = await mallory.post("/api/backtest/runs", json={"strategy_id": sid})
        assert resp.status_code == 404

    async def test_legacy_shape_rerun_matches_stateless_endpoint_stats(self, api):
        # Server-side re-run of the same config+seed == POST /api/backtest —
        # forged client stats are impossible because stats are recomputed.
        stateless = await api.client.post("/api/backtest", json=LEGACY_BODY)
        assert stateless.status_code == 200
        saved = await api.client.post("/api/backtest/runs", json=LEGACY_BODY)
        assert saved.status_code == 201
        run = saved.json()["run"]
        assert run["strategy_id"] is None
        assert run["stats"] == stateless.json()["stats"]
        assert run["config"]["seed"] == 1234
        assert run["config"]["trigger_type"] == "day_change_pct_below"
        assert "entry" not in run["config"]  # legacy echo keeps the old shape

    async def test_legacy_shape_missing_fields_400(self, api):
        resp = await api.client.post(
            "/api/backtest/runs", json={"ticker": "NVDA", "threshold": -2}
        )
        assert resp.status_code == 400
        assert "trigger_type" in resp.json()["error"]

    async def test_validation_failures_400(self, api):
        resp = await api.client.post(
            "/api/backtest/runs", json={**LEGACY_BODY, "days": 3}
        )
        assert resp.status_code == 400
        sid = await _create_strategy(api.client)
        resp = await api.client.post(
            "/api/backtest/runs", json={"strategy_id": sid, "runs": 99}
        )
        assert resp.status_code == 400

    async def test_blank_label_stored_as_null(self, api):
        resp = await api.client.post(
            "/api/backtest/runs", json={**LEGACY_BODY, "label": "   "}
        )
        assert resp.status_code == 201
        assert resp.json()["run"]["label"] is None


class TestTradesTruncation:
    def test_insert_truncates_trades_to_200(self, tmp_path):
        db_file = str(tmp_path / "trunc.db")
        init_db(db_file)
        result = {
            "config": {"ticker": "NVDA", "days": 10, "runs": 1, "seed": 1},
            "stats": {"total_return_pct": 0.0},
            "equity_curve": [{"time": 1, "value": 1.0}],
            "baseline_curve": [{"time": 1, "value": 1.0}],
            "trades": [
                {"time": i, "side": "buy", "price": 1.0, "quantity": 1,
                 "reason": "trigger", "pnl": None}
                for i in range(250)
            ],
            "runs_summary": None,
        }
        conn = get_conn(db_file)
        try:
            run = insert_backtest_run_on_conn(
                conn, user_id="default", strategy_id=None, label=None, result=result
            )
            conn.commit()
        finally:
            conn.close()
        assert len(run["trades"]) == MAX_STORED_TRADES
        assert run["trades"][0]["time"] == 0
        assert run["trades"][-1]["time"] == MAX_STORED_TRADES - 1


@pytest.mark.asyncio
class TestListRuns:
    async def _seed_runs(self, api) -> tuple[str, list[str]]:
        sid = await _create_strategy(api.client)
        ids = []
        for seed in (1, 2):
            resp = await api.client.post(
                "/api/backtest/runs",
                json={"strategy_id": sid, "days": 10, "seed": seed},
            )
            ids.append(resp.json()["run"]["id"])
        resp = await api.client.post(
            "/api/backtest/runs", json={**LEGACY_BODY, "ticker": "AAPL"}
        )
        ids.append(resp.json()["run"]["id"])
        return sid, ids

    async def test_list_newest_first_without_curves(self, api):
        _, ids = await self._seed_runs(api)
        resp = await api.client.get("/api/backtest/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert [r["id"] for r in runs] == list(reversed(ids))
        for r in runs:
            assert set(r) == {
                "id", "strategy_id", "label", "created_at", "ticker",
                "days", "runs", "seed", "stats",
            }

    async def test_strategy_id_filter_cascade(self, api):
        sid, ids = await self._seed_runs(api)
        resp = await api.client.get(f"/api/backtest/runs?strategy_id={sid}")
        runs = resp.json()["runs"]
        assert {r["id"] for r in runs} == set(ids[:2])
        assert all(r["strategy_id"] == sid for r in runs)

    async def test_ticker_filter_is_normalized(self, api):
        await self._seed_runs(api)
        resp = await api.client.get("/api/backtest/runs?ticker=aapl")
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["ticker"] == "AAPL"

    async def test_limit_clamps_and_rejects_garbage(self, api):
        await self._seed_runs(api)
        assert len(
            (await api.client.get("/api/backtest/runs?limit=1")).json()["runs"]
        ) == 1
        # 0 clamps up to 1; 9999 clamps down to 200 (returns everything here).
        assert len(
            (await api.client.get("/api/backtest/runs?limit=0")).json()["runs"]
        ) == 1
        assert len(
            (await api.client.get("/api/backtest/runs?limit=9999")).json()["runs"]
        ) == 3
        assert (
            await api.client.get("/api/backtest/runs?limit=abc")
        ).status_code == 400

    async def test_list_is_user_scoped(self, api):
        await self._seed_runs(api)
        mallory = await api.make_client()
        await mallory.post("/api/auth/login", json={"name": "Mallory"})
        assert (await mallory.get("/api/backtest/runs")).json()["runs"] == []


@pytest.mark.asyncio
class TestDetailAndDelete:
    async def test_get_full_payload_and_404s(self, api):
        resp = await api.client.post("/api/backtest/runs", json=LEGACY_BODY)
        run_id = resp.json()["run"]["id"]

        detail = await api.client.get(f"/api/backtest/runs/{run_id}")
        assert detail.status_code == 200
        run = detail.json()["run"]
        assert set(run) == {
            "id", "strategy_id", "label", "created_at", "config", "stats",
            "equity_curve", "baseline_curve", "trades", "runs_summary",
        }
        assert run["equity_curve"]  # full payload includes the curves

        assert (
            await api.client.get(f"/api/backtest/runs/{uuid.uuid4()}")
        ).status_code == 404

        mallory = await api.make_client()
        await mallory.post("/api/auth/login", json={"name": "Mallory"})
        assert (
            await mallory.get(f"/api/backtest/runs/{run_id}")
        ).status_code == 404
        assert (
            await mallory.delete(f"/api/backtest/runs/{run_id}")
        ).status_code == 404

    async def test_delete_then_404(self, api):
        resp = await api.client.post("/api/backtest/runs", json=LEGACY_BODY)
        run_id = resp.json()["run"]["id"]
        resp = await api.client.delete(f"/api/backtest/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert (
            await api.client.get(f"/api/backtest/runs/{run_id}")
        ).status_code == 404
        assert (
            await api.client.delete(f"/api/backtest/runs/{run_id}")
        ).status_code == 404

    async def test_runs_summary_persists_for_monte_carlo(self, api):
        resp = await api.client.post(
            "/api/backtest/runs", json={**LEGACY_BODY, "runs": 3}
        )
        assert resp.status_code == 201
        run = resp.json()["run"]
        assert run["runs_summary"] is not None
        assert run["runs_summary"]["runs"] == 3
        detail = (await api.client.get(f"/api/backtest/runs/{run['id']}")).json()
        assert detail["run"]["runs_summary"] == run["runs_summary"]
