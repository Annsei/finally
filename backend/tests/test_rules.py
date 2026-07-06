"""Tests for the standing rules engine (M2.2).

Covers:
- CRUD endpoints: POST/GET/PATCH/DELETE /api/rules with the validation matrix
- Rule JSON shape and generated descriptions
- Evaluator (process_rules_once): every trigger_type fires at (not below) its
  boundary, one-shot semantics, re-arm via PATCH, trade-failure path, the
  assistant chat message documenting each activation, and per-rule error
  isolation
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
from app.market.seed_prices import SEED_PRICES
from app.routes.chat import create_chat_router
from app.routes.portfolio import create_portfolio_router
from app.routes.rules import create_rules_router, process_rules_once

RULE_JSON_KEYS = {
    "id", "ticker", "description", "trigger_type", "threshold", "side",
    "quantity", "status", "created_at", "last_fired_at", "fire_count",
}

NVDA_PRICE = SEED_PRICES["NVDA"]  # 800.0


@pytest_asyncio.fixture
async def rules_env(tmp_path, monkeypatch):
    """Client + db_file + price cache for evaluator tests.

    Registers the rules, portfolio, and chat routers on an isolated app so
    tests can create rules over HTTP, drive the cache directly, run
    process_rules_once synchronously, and verify effects through the public
    endpoints (including GET /api/chat/ for rule-fired messages).
    """
    db_file = str(tmp_path / "rules_test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MOCK", "true")
    init_db(db_file)

    cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_rules_router(cache, db_file))
    test_app.include_router(create_portfolio_router(cache, db_file))
    test_app.include_router(create_chat_router(cache, db_file))

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, db_file=db_file, cache=cache)


async def _create_rule(client, **overrides) -> dict:
    """POST a rule with sensible defaults; returns the created rule JSON."""
    body: dict = {
        "ticker": "NVDA",
        "trigger_type": "day_change_pct_below",
        "threshold": -3,
        "side": "buy",
        "quantity": 5,
    }
    body.update(overrides)
    resp = await client.post("/api/rules", json=body)
    assert resp.status_code == 200, resp.json()
    return resp.json()["rule"]


@pytest.mark.asyncio
class TestRulesCrud:
    """CRUD + validation matrix via the app_client fixture (rules router wired)."""

    async def test_create_returns_full_rule_json(self, app_client):
        rule = await _create_rule(app_client)
        assert set(rule.keys()) == RULE_JSON_KEYS
        assert rule["ticker"] == "NVDA"
        assert rule["trigger_type"] == "day_change_pct_below"
        assert rule["threshold"] == -3
        assert rule["side"] == "buy"
        assert rule["quantity"] == 5
        assert rule["status"] == "active"
        assert rule["fire_count"] == 0
        assert rule["last_fired_at"] is None
        assert rule["description"] == "Buy 5 NVDA when day change <= -3%"

    async def test_create_keeps_explicit_description(self, app_client):
        rule = await _create_rule(app_client, description="Dip-buy NVDA on a bad day")
        assert rule["description"] == "Dip-buy NVDA on a bad day"

    async def test_generated_descriptions_per_trigger(self, app_client):
        cases = [
            ({"trigger_type": "price_above", "threshold": 850, "side": "sell", "quantity": 2},
             "Sell 2 NVDA when price >= $850"),
            ({"trigger_type": "price_below", "threshold": 700}, "Buy 5 NVDA when price <= $700"),
            ({"trigger_type": "day_change_pct_above", "threshold": 4},
             "Buy 5 NVDA when day change >= 4%"),
        ]
        for overrides, expected in cases:
            rule = await _create_rule(app_client, **overrides)
            assert rule["description"] == expected

    @pytest.mark.parametrize(
        ("overrides", "expected_error"),
        [
            pytest.param({"ticker": "ZZZZ"}, "Ticker not found in price cache", id="unknown-ticker"),
            pytest.param({"trigger_type": "price_crosses"}, "trigger_type must be one of", id="bad-trigger"),
            pytest.param({"side": "hold"}, "Side must be 'buy' or 'sell'", id="bad-side"),
            pytest.param({"quantity": 0}, "Quantity must be greater than 0", id="zero-qty"),
            pytest.param({"quantity": -1}, "Quantity must be greater than 0", id="negative-qty"),
            pytest.param(
                {"trigger_type": "price_above", "threshold": 0},
                "Threshold must be greater than 0 for price triggers", id="zero-price-threshold",
            ),
            pytest.param(
                {"trigger_type": "price_below", "threshold": -10},
                "Threshold must be greater than 0 for price triggers", id="negative-price-threshold",
            ),
        ],
    )
    async def test_create_validation_matrix(self, app_client, overrides, expected_error):
        body = {
            "ticker": "NVDA", "trigger_type": "day_change_pct_below",
            "threshold": -3, "side": "buy", "quantity": 5,
        }
        body.update(overrides)
        resp = await app_client.post("/api/rules", json=body)
        assert resp.status_code == 400
        assert expected_error in resp.json()["error"]

        rules = (await app_client.get("/api/rules")).json()["rules"]
        assert rules == []

    async def test_negative_threshold_ok_for_day_change_triggers(self, app_client):
        rule = await _create_rule(app_client, trigger_type="day_change_pct_below", threshold=-5)
        assert rule["threshold"] == -5

    async def test_list_newest_first(self, app_client):
        first = await _create_rule(app_client, ticker="AAPL")
        second = await _create_rule(app_client, ticker="MSFT")
        third = await _create_rule(app_client, ticker="NVDA")

        rules = (await app_client.get("/api/rules")).json()["rules"]
        assert [r["id"] for r in rules] == [third["id"], second["id"], first["id"]]

    async def test_list_filters_by_status(self, app_client):
        active = await _create_rule(app_client, ticker="AAPL")
        paused = await _create_rule(app_client, ticker="MSFT")
        resp = await app_client.patch(f"/api/rules/{paused['id']}", json={"status": "paused"})
        assert resp.status_code == 200

        only_paused = (await app_client.get("/api/rules?status=paused")).json()["rules"]
        assert [r["id"] for r in only_paused] == [paused["id"]]

        only_active = (await app_client.get("/api/rules?status=active")).json()["rules"]
        assert [r["id"] for r in only_active] == [active["id"]]

        fired = (await app_client.get("/api/rules?status=fired")).json()["rules"]
        assert fired == []

        everything = (await app_client.get("/api/rules?status=all")).json()["rules"]
        assert len(everything) == 2

    async def test_list_invalid_status_returns_400(self, app_client):
        resp = await app_client.get("/api/rules?status=bogus")
        assert resp.status_code == 400
        assert "status must be one of" in resp.json()["error"]

    async def test_patch_pause_and_reactivate(self, app_client):
        rule = await _create_rule(app_client)

        resp = await app_client.patch(f"/api/rules/{rule['id']}", json={"status": "paused"})
        assert resp.status_code == 200
        assert resp.json()["rule"]["status"] == "paused"
        assert set(resp.json()["rule"].keys()) == RULE_JSON_KEYS

        resp = await app_client.patch(f"/api/rules/{rule['id']}", json={"status": "active"})
        assert resp.status_code == 200
        assert resp.json()["rule"]["status"] == "active"

    async def test_patch_unknown_id_returns_404(self, app_client):
        resp = await app_client.patch("/api/rules/nope", json={"status": "paused"})
        assert resp.status_code == 404
        assert resp.json() == {"error": "Rule not found"}

    @pytest.mark.parametrize("status", ["fired", "deleted", "ACTIVE!"])
    async def test_patch_invalid_status_returns_400(self, app_client, status):
        rule = await _create_rule(app_client)
        resp = await app_client.patch(f"/api/rules/{rule['id']}", json={"status": status})
        assert resp.status_code == 400
        assert resp.json() == {"error": "status must be 'active' or 'paused'"}

    async def test_delete_returns_rule_and_removes_it(self, app_client):
        rule = await _create_rule(app_client)
        resp = await app_client.delete(f"/api/rules/{rule['id']}")
        assert resp.status_code == 200
        assert resp.json()["rule"]["id"] == rule["id"]

        rules = (await app_client.get("/api/rules")).json()["rules"]
        assert rules == []

        again = await app_client.delete(f"/api/rules/{rule['id']}")
        assert again.status_code == 404

    async def test_delete_unknown_id_returns_404(self, app_client):
        resp = await app_client.delete("/api/rules/nope")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Rule not found"}


def _get_rule_row(db_file: str, rule_id: str):
    conn = get_conn(db_file)
    try:
        return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    finally:
        conn.close()


@pytest.mark.asyncio
class TestRulesEvaluatorTriggers:
    """Each trigger_type fires at its boundary and not just short of it."""

    @pytest.mark.parametrize(
        ("trigger_type", "threshold", "no_fire_price", "fire_price"),
        [
            # NVDA prev_close is the 800 seed price; price triggers compare
            # the raw price, day-change triggers compare (price-800)/800*100.
            pytest.param("price_above", 810.0, 809.99, 810.0, id="price_above"),
            pytest.param("price_below", 790.0, 790.01, 790.0, id="price_below"),
            pytest.param("day_change_pct_above", 2.0, 815.99, 816.0, id="day_change_pct_above"),
            pytest.param("day_change_pct_below", -3.0, 776.01, 776.0, id="day_change_pct_below"),
        ],
    )
    async def test_boundary_semantics(
        self, rules_env, trigger_type, threshold, no_fire_price, fire_price
    ):
        rule = await _create_rule(
            rules_env.client, trigger_type=trigger_type, threshold=threshold, quantity=1
        )

        rules_env.cache.update("NVDA", no_fire_price)
        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts == {"fired": 0, "trade_failed": 0, "skipped": 1}
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "active" and row["fire_count"] == 0

        rules_env.cache.update("NVDA", fire_price)
        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts == {"fired": 1, "trade_failed": 0, "skipped": 0}
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "fired"
        assert row["fire_count"] == 1
        assert row["last_fired_at"] is not None

        # The market trade executed at the fire price
        portfolio = (await rules_env.client.get("/api/portfolio/")).json()
        tickers = {p["ticker"]: p for p in portfolio["positions"]}
        assert "NVDA" in tickers
        assert portfolio["cash"] == pytest.approx(10000.0 - fire_price)


@pytest.mark.asyncio
class TestRulesEvaluatorLifecycle:
    async def test_one_shot_does_not_refire(self, rules_env):
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1
        )  # already true at 800 — fires on the first pass

        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts["fired"] == 1

        # Condition still true, but the rule is consumed
        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts == {"fired": 0, "trade_failed": 0, "skipped": 0}
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "fired" and row["fire_count"] == 1

    async def test_rearm_via_patch_fires_again(self, rules_env):
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1
        )
        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1

        resp = await rules_env.client.patch(
            f"/api/rules/{rule['id']}", json={"status": "active"}
        )
        assert resp.status_code == 200

        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "fired"
        assert row["fire_count"] == 2

    async def test_paused_rule_is_not_evaluated(self, rules_env):
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1
        )
        resp = await rules_env.client.patch(
            f"/api/rules/{rule['id']}", json={"status": "paused"}
        )
        assert resp.status_code == 200

        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts == {"fired": 0, "trade_failed": 0, "skipped": 0}
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "paused" and row["fire_count"] == 0

    async def test_trade_failure_still_consumes_rule_and_documents_it(self, rules_env):
        """Insufficient cash: rule moves to 'fired' (one shot spent) and the
        chat message + actions record the failed trade."""
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1000
        )  # 1000 * $800 >> $10k cash

        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts == {"fired": 0, "trade_failed": 1, "skipped": 0}
        row = _get_rule_row(rules_env.db_file, rule["id"])
        assert row["status"] == "fired"
        assert row["fire_count"] == 1
        assert row["last_fired_at"] is not None

        messages = (await rules_env.client.get("/api/chat/")).json()["messages"]
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == (
            f"Rule fired: {rule['description']} — trade failed: Insufficient cash."
        )
        assert msg["actions"]["rule_id"] == rule["id"]
        assert msg["actions"]["trades"][0]["status"] == "failed"
        assert msg["actions"]["trades"][0]["error"] == "Insufficient cash"

        # No money moved
        portfolio = (await rules_env.client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == 10000.0
        assert portfolio["positions"] == []

    async def test_fired_rule_writes_chat_message_visible_in_history(self, rules_env):
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=2,
            description="Momentum buy on NVDA",
        )
        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts["fired"] == 1

        messages = (await rules_env.client.get("/api/chat/")).json()["messages"]
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "assistant"
        assert msg["kind"] == "rule"  # M2 Wave 2: rule activations are kind='rule'
        assert msg["content"] == (
            f"Rule fired: Momentum buy on NVDA — executed at ${NVDA_PRICE:.2f}."
        )
        assert isinstance(msg["actions"], dict)
        assert msg["actions"]["rule_id"] == rule["id"]
        trade = msg["actions"]["trades"][0]
        assert trade["status"] == "executed"
        assert trade["ticker"] == "NVDA"
        assert trade["quantity"] == 2
        assert trade["price"] == NVDA_PRICE
        assert "trade_id" in trade

    async def test_buy_fires_at_ask_sell_fires_at_bid(self, rules_env):
        """Rule-driven market trades use the same bid/ask fills as manual ones."""
        rules_env.cache.update("MSFT", 420.0, bid=419.0, ask=421.0)

        buy_rule = await _create_rule(
            rules_env.client, ticker="MSFT", trigger_type="price_above",
            threshold=400.0, side="buy", quantity=1,
        )
        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1
        buy_msg = (await rules_env.client.get("/api/chat/")).json()["messages"][-1]
        assert buy_msg["actions"]["rule_id"] == buy_rule["id"]
        assert buy_msg["actions"]["trades"][0]["price"] == 421.0  # ask

        sell_rule = await _create_rule(
            rules_env.client, ticker="MSFT", trigger_type="price_above",
            threshold=400.0, side="sell", quantity=1,
        )
        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1
        sell_msg = (await rules_env.client.get("/api/chat/")).json()["messages"][-1]
        assert sell_msg["actions"]["rule_id"] == sell_rule["id"]
        assert sell_msg["actions"]["trades"][0]["price"] == 419.0  # bid

    async def test_snapshot_recorded_with_fired_trade(self, rules_env):
        await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1
        )
        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1

        snapshots = (await rules_env.client.get("/api/portfolio/history")).json()["snapshots"]
        assert len(snapshots) == 1

    async def test_per_rule_error_isolation(self, rules_env, monkeypatch):
        """An unexpected error on one rule is rolled back and does not stop the
        pass; the broken rule stays active, the next rule still fires."""
        import app.routes.rules as rules_module

        first = await _create_rule(
            rules_env.client, ticker="AAPL", trigger_type="price_above",
            threshold=100.0, quantity=1,
        )
        second = await _create_rule(
            rules_env.client, ticker="MSFT", trigger_type="price_above",
            threshold=100.0, quantity=1,
        )

        real_execute = rules_module.execute_trade_on_conn
        calls = {"count": 0}

        def exploding_execute(conn, price_cache, ticker, side, quantity, commission_bps=0.0):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("boom on first rule")
            return real_execute(
                conn, price_cache, ticker, side, quantity, commission_bps=commission_bps
            )

        monkeypatch.setattr(rules_module, "execute_trade_on_conn", exploding_execute)

        counts = process_rules_once(rules_env.db_file, rules_env.cache)
        assert counts["fired"] == 1  # the second rule

        first_row = _get_rule_row(rules_env.db_file, first["id"])
        assert first_row["status"] == "active" and first_row["fire_count"] == 0
        second_row = _get_rule_row(rules_env.db_file, second["id"])
        assert second_row["status"] == "fired" and second_row["fire_count"] == 1

    async def test_rule_fired_actions_json_round_trips(self, rules_env):
        """The stored actions column is valid JSON with the documented shape."""
        rule = await _create_rule(
            rules_env.client, trigger_type="price_above", threshold=700.0, quantity=1
        )
        assert process_rules_once(rules_env.db_file, rules_env.cache)["fired"] == 1

        conn = get_conn(rules_env.db_file)
        try:
            row = conn.execute(
                "SELECT actions FROM chat_messages WHERE user_id = 'default'"
            ).fetchone()
        finally:
            conn.close()
        actions = json.loads(row["actions"])
        assert set(actions.keys()) == {"trades", "rule_id"}
        assert actions["rule_id"] == rule["id"]
        assert actions["trades"][0]["status"] == "executed"
