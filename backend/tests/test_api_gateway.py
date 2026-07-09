"""P3 §2-§5 — ApiKeyGatewayMiddleware: Bearer auth, rate limit, guardrails, audit.

Covers the Bearer resolution matrix (valid → user, unknown → 401, frozen →
403 with a throttled denied audit, Bearer wins over cookie, last_used
throttling), the no-Authorization
byte-identical passthrough (core invariant — including SSE streaming), the
per-key token bucket (burst 10 / refill 5/s / 429 / throttled audit), the
three §4 guardrails plus the UTC-midnight daily-cap reset, the §5 audit
matrix (ok/denied/error/rate_limited, digest truncation, GETs never
audited), the keys-cannot-manage-keys privilege boundary, and the
zero-leak invariant (plaintext/hash never in audit rows, non-creation
responses, or logs).
"""

# The gateway_env fixture is imported from tests.gateway_fixtures (conftest is
# frozen for P3); every test parameter named gateway_env "shadows" that import.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn

# Imported fixtures/helpers (pytest picks fixtures up from this namespace).
from tests.gateway_fixtures import (  # noqa: F401
    audit_rows,
    bearer,
    build_app,
    create_key,
    gateway_env,
    key_row,
    login,
)

TRADE = {"ticker": "AAPL", "side": "buy", "quantity": 1}


async def _read_sse_head(
    app, extra_headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[int, bytes]:
    """Drive the ASGI app directly and read the first SSE body chunks.

    httpx's ASGITransport buffers the entire response, so an infinite SSE
    stream can't be consumed through it — this harness collects raw ASGI
    messages until a ``data:`` frame arrives, then cancels the app task
    (exactly what a client disconnect does to the streaming generator).
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/stream/prices",
        "raw_path": b"/api/stream/prices",
        "query_string": b"",
        "root_path": "",
        "headers": list(extra_headers or []),
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    status: list[int] = []
    body = bytearray()
    got_data = asyncio.Event()

    async def receive():
        await asyncio.Event().wait()  # the client never disconnects on its own

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
            if b"data:" in body or not message.get("more_body", False):
                got_data.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(got_data.wait(), timeout=5.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return status[0], bytes(body)


async def _alice_with_key(env, **key_fields):
    """Login alice on the default client and mint her a key."""
    await login(env.client, "alice")
    return await create_key(env.client, **key_fields)


class TestBearerResolution:
    async def test_valid_key_resolves_to_owner(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env)
        resp = await gateway_env.client.post("/api/portfolio/trade", json=TRADE)
        assert resp.status_code == 200  # cookie-side buy for alice

        anon = await gateway_env.make_client()  # no cookies at all
        portfolio = (await anon.get("/api/portfolio/", headers=bearer(key))).json()
        assert [p["ticker"] for p in portfolio["positions"]] == ["AAPL"]
        me = (await anon.get("/api/auth/me", headers=bearer(key))).json()
        assert me["user"]["id"] == "alice"

    async def test_bearer_wins_over_cookie(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env)
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        me = (await bob.get("/api/auth/me", headers=bearer(key))).json()
        assert me["user"]["id"] == "alice"  # not bob, despite bob's cookie

    async def test_unknown_key_401_and_not_audited(self, gateway_env):
        resp = await gateway_env.client.get(
            "/api/portfolio/", headers=bearer("fk_this-key-does-not-exist")
        )
        assert resp.status_code == 401
        assert resp.json() == {"error": "Invalid API key"}
        assert audit_rows(gateway_env.db_file) == []

    async def test_empty_bearer_token_401(self, gateway_env):
        resp = await gateway_env.client.get(
            "/api/portfolio/", headers={"Authorization": "Bearer "}
        )
        assert resp.status_code == 401

    async def test_non_bearer_authorization_passes_through(self, gateway_env):
        # Other schemes are not ours: request proceeds as anonymous cookie path.
        resp = await gateway_env.client.get(
            "/api/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["id"] == "default"

    async def test_frozen_key_403_and_audited_denied(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"frozen": True})
        resp = await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert resp.status_code == 403
        assert resp.json() == {"error": "API key is frozen"}
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert [r["result"] for r in rows] == ["denied"]
        assert rows[0]["status_code"] == 403

    async def test_frozen_denied_audit_throttled_to_10s(self, gateway_env):
        # Mirrors the rate_limited throttle: hammering a frozen key gets a 403
        # every time, but only one denied audit row per key per 10s window.
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"frozen": True})
        for _ in range(5):  # five 403s at the same instant → one audit row
            resp = await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
            assert resp.status_code == 403
            assert resp.json() == {"error": "API key is frozen"}
        denied = [
            r for r in audit_rows(gateway_env.db_file, info["id"])
            if r["result"] == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["status_code"] == 403

        gateway_env.time.advance(10)  # window elapsed → the next 403 is audited
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 403
        denied = [
            r for r in audit_rows(gateway_env.db_file, info["id"])
            if r["result"] == "denied"
        ]
        assert len(denied) == 2

    async def test_unfreeze_restores_access_immediately(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"frozen": True})
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 403
        await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"frozen": False})
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 200

    async def test_revoked_key_401(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 200
        await gateway_env.client.delete(f"/api/keys/{info['id']}")
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 401

    async def test_last_used_written_once_then_throttled_60s(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        first = key_row(gateway_env.db_file, info["id"])["last_used_at"]
        assert first is not None

        await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert key_row(gateway_env.db_file, info["id"])["last_used_at"] == first

        gateway_env.time.advance(61)
        await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert key_row(gateway_env.db_file, info["id"])["last_used_at"] != first


class TestNoBearerPassthrough:
    """Core invariant: requests without a Bearer header are byte-identical."""

    PATHS = ["/api/health", "/api/portfolio/", "/api/watchlist/", "/api/auth/me"]

    async def test_anonymous_gets_byte_identical(self, gateway_env):
        plain_app = build_app(
            gateway_env.db_file, gateway_env.price_cache, with_middleware=False
        )
        async with AsyncClient(
            transport=ASGITransport(app=plain_app), base_url="http://test"
        ) as plain:
            for path in self.PATHS:
                wrapped = await gateway_env.client.get(path)
                unwrapped = await plain.get(path)
                assert wrapped.status_code == unwrapped.status_code, path
                assert wrapped.content == unwrapped.content, path
                assert wrapped.headers == unwrapped.headers, path

    async def test_cookie_flow_byte_identical(self, gateway_env):
        plain_app = build_app(
            gateway_env.db_file, gateway_env.price_cache, with_middleware=False
        )
        async with AsyncClient(
            transport=ASGITransport(app=plain_app), base_url="http://test"
        ) as plain:
            a = await gateway_env.client.post("/api/auth/login", json={"name": "carol"})
            b = await plain.post("/api/auth/login", json={"name": "carol"})
            assert a.status_code == b.status_code == 200
            assert a.content == b.content
            assert a.headers["set-cookie"] == b.headers["set-cookie"]
            me_a = await gateway_env.client.get("/api/auth/me")
            me_b = await plain.get("/api/auth/me")
            assert me_a.content == me_b.content

    async def test_forged_cookie_still_falls_back_to_default(self, gateway_env):
        resp = await gateway_env.client.get(
            "/api/auth/me", headers={"cookie": "finally_session=alice.deadbeef"}
        )
        assert resp.json()["user"]["id"] == "default"

    async def test_anonymous_trade_unaffected_and_not_audited(self, gateway_env):
        resp = await gateway_env.client.post("/api/portfolio/trade", json=TRADE)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert audit_rows(gateway_env.db_file) == []

    async def test_sse_stream_flows_through_middleware(self, gateway_env):
        status, body = await _read_sse_head(gateway_env.app)
        assert status == 200
        assert b"retry: 1000" in body
        assert b"data:" in body
        assert b'"AAPL"' in body

    async def test_sse_stream_with_bearer_behaves_identically(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        status, body = await _read_sse_head(
            gateway_env.app, [(b"authorization", f"Bearer {key}".encode())]
        )
        assert status == 200
        assert b"retry: 1000" in body
        assert b"data:" in body
        # GET — rate limited but never audited, and the stream is untouched.
        assert audit_rows(gateway_env.db_file, info["id"]) == []


class TestKeysCannotManageKeys:
    async def test_bearer_rejected_on_every_management_endpoint(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        headers = bearer(key)
        client = gateway_env.client
        expected = {"error": "Keys cannot manage keys"}

        for resp in [
            await client.get("/api/keys", headers=headers),
            await client.post("/api/keys", json={"label": "escalated"}, headers=headers),
            await client.patch(
                f"/api/keys/{info['id']}", json={"frozen": False}, headers=headers
            ),
            await client.delete(f"/api/keys/{info['id']}", headers=headers),
            await client.get(f"/api/keys/{info['id']}/audit", headers=headers),
        ]:
            assert resp.status_code == 403
            assert resp.json() == expected

        # Nothing minted, nothing revoked.
        keys = (await client.get("/api/keys")).json()["keys"]
        assert [k["id"] for k in keys] == [info["id"]]

    async def test_key_cannot_raise_its_own_limits(self, gateway_env):
        key, info = await _alice_with_key(gateway_env, max_order_qty=1)
        resp = await gateway_env.client.patch(
            f"/api/keys/{info['id']}", json={"max_order_qty": None}, headers=bearer(key)
        )
        assert resp.status_code == 403
        assert key_row(gateway_env.db_file, info["id"])["max_order_qty"] == 1.0

    async def test_route_level_check_holds_without_middleware(self, gateway_env):
        # Defense in depth: even mounted without the gateway, /api/keys
        # rejects a raw Bearer header instead of falling back to the cookie.
        plain_app = build_app(
            gateway_env.db_file, gateway_env.price_cache, with_middleware=False
        )
        async with AsyncClient(
            transport=ASGITransport(app=plain_app), base_url="http://test"
        ) as plain:
            await login(plain, "alice")
            resp = await plain.get("/api/keys", headers=bearer("fk_anything"))
            assert resp.status_code == 403
            assert resp.json() == {"error": "Keys cannot manage keys"}


class TestRateLimit:
    async def test_burst_of_10_then_429(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env)
        for i in range(10):
            resp = await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
            assert resp.status_code == 200, f"request {i + 1} should pass"
        resp = await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert resp.status_code == 429
        assert resp.json() == {"error": "Rate limited"}

    async def test_refill_rate_is_5_per_second(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env)
        for _ in range(10):
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 429

        gateway_env.time.advance(0.2)  # exactly one token
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 200
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 429

        gateway_env.time.advance(1.0)  # five tokens
        for i in range(5):
            resp = await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
            assert resp.status_code == 200, f"refilled request {i + 1}"
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 429

    async def test_rate_limited_audit_throttled_to_10s(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        for _ in range(10):
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        for _ in range(3):  # three 429s at the same instant → one audit row
            assert (
                await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
            ).status_code == 429
        limited = [
            r for r in audit_rows(gateway_env.db_file, info["id"])
            if r["result"] == "rate_limited"
        ]
        assert len(limited) == 1
        assert limited[0]["status_code"] == 429

        gateway_env.time.advance(10)  # bucket refills too — drain it again
        for _ in range(10):
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        ).status_code == 429
        limited = [
            r for r in audit_rows(gateway_env.db_file, info["id"])
            if r["result"] == "rate_limited"
        ]
        assert len(limited) == 2

    async def test_buckets_are_per_key(self, gateway_env):
        key_a, _ = await _alice_with_key(gateway_env, label="a")
        key_b, _ = await create_key(gateway_env.client, label="b")
        for _ in range(10):
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key_a))
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key_a))
        ).status_code == 429
        assert (
            await gateway_env.client.get("/api/portfolio/", headers=bearer(key_b))
        ).status_code == 200

    async def test_cookie_requests_never_rate_limited(self, gateway_env):
        await login(gateway_env.client, "alice")
        for _ in range(15):
            assert (await gateway_env.client.get("/api/portfolio/")).status_code == 200


class TestGuardrails:
    async def test_ticker_not_allowed_403_audited_denied(self, gateway_env):
        key, info = await _alice_with_key(gateway_env, allowed_tickers=["AAPL", "MSFT"])
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "TSLA", "side": "buy", "quantity": 1},
            headers=bearer(key),
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "Ticker not allowed for this key"}
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert [r["result"] for r in rows] == ["denied"]
        assert "TSLA" in rows[0]["payload_digest"]

    async def test_ticker_comparison_is_case_normalized(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env, allowed_tickers=["aapl"])
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "aapl", "side": "buy", "quantity": 1},
            headers=bearer(key),
        )
        assert resp.status_code == 200

    async def test_quantity_over_limit_403_at_limit_ok(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env, max_order_qty=5)
        over = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 6},
            headers=bearer(key),
        )
        assert over.status_code == 403
        assert over.json() == {"error": "Quantity exceeds key limit"}
        at_limit = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 5},
            headers=bearer(key),
        )
        assert at_limit.status_code == 200

    async def test_numeric_string_quantity_cannot_bypass_limit(self, gateway_env):
        # pydantic lax mode coerces "6" → 6.0 at the route, so the guardrail
        # must compare numeric strings too — otherwise this is a bypass.
        key, _ = await _alice_with_key(gateway_env, max_order_qty=5)
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": "6"},
            headers=bearer(key),
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "Quantity exceeds key limit"}

    async def test_ticker_check_precedes_quantity_check(self, gateway_env):
        key, _ = await _alice_with_key(
            gateway_env, allowed_tickers=["AAPL"], max_order_qty=5
        )
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "TSLA", "side": "buy", "quantity": 100},
            headers=bearer(key),
        )
        assert resp.json() == {"error": "Ticker not allowed for this key"}

    async def test_daily_cap_reached_403(self, gateway_env):
        key, info = await _alice_with_key(gateway_env, daily_trade_cap=2)
        for _ in range(2):
            resp = await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
            assert resp.status_code == 200
        third = await gateway_env.client.post(
            "/api/portfolio/trade", json=TRADE, headers=bearer(key)
        )
        assert third.status_code == 403
        assert third.json() == {"error": "Daily trade cap reached"}
        results = [r["result"] for r in audit_rows(gateway_env.db_file, info["id"])]
        assert results == ["ok", "ok", "denied"]

    async def test_daily_cap_counts_orders_endpoint_too(self, gateway_env):
        key, _ = await _alice_with_key(gateway_env, daily_trade_cap=2)
        order = {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 1,
            "kind": "limit",
            "limit_price": 1.0,  # far below market → rests open
        }
        assert (
            await gateway_env.client.post(
                "/api/portfolio/orders", json=order, headers=bearer(key)
            )
        ).status_code == 200
        assert (
            await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
        ).status_code == 200
        blocked = await gateway_env.client.post(
            "/api/portfolio/orders", json=order, headers=bearer(key)
        )
        assert blocked.status_code == 403
        assert blocked.json() == {"error": "Daily trade cap reached"}

    async def test_daily_cap_resets_at_utc_midnight(self, gateway_env):
        key, info = await _alice_with_key(gateway_env, daily_trade_cap=1)
        assert (
            await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
        ).status_code == 200
        assert (
            await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
        ).status_code == 403

        # Age today's ok rows past the UTC midnight boundary → cap resets.
        conn = get_conn(gateway_env.db_file)
        try:
            conn.execute(
                "UPDATE api_audit SET created_at = '2000-01-01T00:00:00+00:00' "
                "WHERE key_id = ? AND result = 'ok'",
                (info["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        assert (
            await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
        ).status_code == 200

    async def test_denied_trades_do_not_consume_the_cap(self, gateway_env):
        key, _ = await _alice_with_key(
            gateway_env, allowed_tickers=["AAPL"], daily_trade_cap=1
        )
        for _ in range(3):  # denied by ticker guardrail — never 'ok' rows
            resp = await gateway_env.client.post(
                "/api/portfolio/trade",
                json={"ticker": "TSLA", "side": "buy", "quantity": 1},
                headers=bearer(key),
            )
            assert resp.status_code == 403
        # The single capped trade is still available.
        assert (
            await gateway_env.client.post(
                "/api/portfolio/trade", json=TRADE, headers=bearer(key)
            )
        ).status_code == 200

    async def test_unparseable_body_passes_through_to_route(self, gateway_env):
        key, info = await _alice_with_key(gateway_env, allowed_tickers=["AAPL"])
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            content=b"{not json",
            headers={**bearer(key), "content-type": "application/json"},
        )
        assert resp.status_code == 422  # the route rejects it, not the guardrail
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert [r["result"] for r in rows] == ["error"]
        assert rows[0]["payload_digest"] is None

    async def test_guardrails_do_not_apply_to_cookie_traffic(self, gateway_env):
        # The same user's cookie session is not constrained by their key.
        key, _ = await _alice_with_key(gateway_env, allowed_tickers=["MSFT"])
        resp = await gateway_env.client.post("/api/portfolio/trade", json=TRADE)
        assert resp.status_code == 200
        assert audit_rows(gateway_env.db_file) == []

    async def test_unconstrained_key_trades_freely(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        resp = await gateway_env.client.post(
            "/api/portfolio/trade", json=TRADE, headers=bearer(key)
        )
        assert resp.status_code == 200
        assert [r["result"] for r in audit_rows(gateway_env.db_file, info["id"])] == ["ok"]


class TestAuditLedger:
    async def test_ok_row_shape_on_successful_trade(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.post(
            "/api/portfolio/trade", json=TRADE, headers=bearer(key)
        )
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["method"] == "POST"
        assert row["endpoint"] == "/api/portfolio/trade"
        assert row["result"] == "ok"
        assert row["status_code"] == 200
        assert row["user_id"] == "alice"
        digest = json.loads(row["payload_digest"])
        assert digest == TRADE

    async def test_error_row_on_route_400(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        resp = await gateway_env.client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 100000},  # > cash
            headers=bearer(key),
        )
        assert resp.status_code == 400
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert [(r["result"], r["status_code"]) for r in rows] == [("error", 400)]

    async def test_gets_are_never_audited(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        await gateway_env.client.get("/api/portfolio/", headers=bearer(key))
        await gateway_env.client.get("/api/portfolio/trades", headers=bearer(key))
        await gateway_env.client.get("/api/watchlist/", headers=bearer(key))
        assert audit_rows(gateway_env.db_file, info["id"]) == []

    async def test_unlisted_endpoints_not_audited(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        # POST /api/backtest (stateless engine) is NOT on the §5 audit surface.
        resp = await gateway_env.client.post(
            "/api/backtest", json={}, headers=bearer(key)
        )
        assert resp.status_code in (400, 422)
        assert audit_rows(gateway_env.db_file, info["id"]) == []

    async def test_watchlist_mutations_audited(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        add = await gateway_env.client.post(
            "/api/watchlist/", json={"ticker": "PYPL"}, headers=bearer(key)
        )
        assert add.status_code == 200
        remove = await gateway_env.client.delete(
            "/api/watchlist/PYPL", headers=bearer(key)
        )
        assert remove.status_code == 200
        rows = audit_rows(gateway_env.db_file, info["id"])
        assert [(r["method"], r["result"]) for r in rows] == [("POST", "ok"), ("DELETE", "ok")]
        assert rows[1]["payload_digest"] is None  # DELETE has no body

    async def test_digest_is_compact_json_truncated_to_200(self, gateway_env):
        key, info = await _alice_with_key(gateway_env)
        padded = {**TRADE, "note": "x" * 500}  # extra fields are ignored by the route
        resp = await gateway_env.client.post(
            "/api/portfolio/trade", json=padded, headers=bearer(key)
        )
        assert resp.status_code == 200
        row = audit_rows(gateway_env.db_file, info["id"])[0]
        assert len(row["payload_digest"]) == 200
        assert '"ticker":"AAPL"' in row["payload_digest"]  # compact separators

    async def test_zero_leak_of_plaintext_and_hash(self, gateway_env, caplog):
        """The creation response is the ONLY place the plaintext ever appears:
        not in audit rows, not in later responses, not in logs — and the
        sha256 hash never appears anywhere outside the api_keys table."""
        with caplog.at_level(logging.DEBUG):
            await login(gateway_env.client, "alice")
            key, info = await create_key(
                gateway_env.client, allowed_tickers=["AAPL"], daily_trade_cap=5
            )
            headers = bearer(key)
            client = gateway_env.client
            # ok / denied / error / rate_limited traffic:
            await client.post("/api/portfolio/trade", json=TRADE, headers=headers)
            await client.post(
                "/api/portfolio/trade",
                json={"ticker": "TSLA", "side": "buy", "quantity": 1},
                headers=headers,
            )
            await client.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "side": "buy", "quantity": 99999},
                headers=headers,
            )
            for _ in range(8):  # drain the bucket (10 used above) → 429s
                await client.get("/api/portfolio/", headers=headers)
            # Unknown-key rejection logs a prefix — of that foreign token only.
            await client.get("/api/portfolio/", headers=bearer("fk_someone-elses-key"))

            responses = [
                (await client.get("/api/keys")).text,
                (await client.get(f"/api/keys/{info['id']}/audit")).text,
                (await client.get("/api/portfolio/", headers=headers)).text,
            ]

        key_hash = hashlib.sha256(key.encode()).hexdigest()
        for text in responses:
            assert key not in text
            assert key_hash not in text
        for row in audit_rows(gateway_env.db_file):
            for column in row.keys():
                value = row[column]
                if isinstance(value, str):
                    assert key not in value
                    assert key_hash not in value
        log_text = caplog.text
        assert key not in log_text
        assert key_hash not in log_text
