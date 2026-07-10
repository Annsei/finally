"""Shared fixtures/helpers for the P3 API-key gateway + keys tests (new files).

Existing test files (including conftest.py) are frozen for P3, so the new
test modules import these fixtures into their own namespace instead —
pytest collects fixtures from a test module's namespace, imported or not.

``gateway_env`` builds the same app the arena fixture does (isolated temp
SQLite DB, seeded PriceCache, FakeMarketSource on app.state) plus:

- the pure-ASGI ``ApiKeyGatewayMiddleware`` wired with a ``FakeTime``
  monotonic clock (drive the token bucket / write throttles by advancing
  ``env.time``; it starts full at 10 tokens per key),
- the /api/keys management router,
- ``make_client()`` for independent cookie jars (one per simulated user).

NOTE on the fake clock: it never advances on its own, so a single test may
send at most 10 Bearer requests per key before the bucket empties — advance
``env.time`` between batches when a test needs more.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api_gateway import ApiKeyGatewayMiddleware
from app.db.connection import get_conn, init_db
from app.market import PriceCache, create_stream_router
from app.market.seed_prices import SEED_PRICES
from app.settings import CLASSROOM_SERVER, RuntimeSettings
from tests.conftest import FakeMarketSource, FakeTime


def server_settings() -> RuntimeSettings:
    """Classroom-server RuntimeSettings for the runtime-mode gating tests."""
    return RuntimeSettings(
        mode=CLASSROOM_SERVER,
        bind_host="0.0.0.0",
        server_auth_secret="classroom-secret-123",
        admin_token="admin-secret-123456",
        single_replica=True,
    )


def build_app(
    db_file: str,
    price_cache: PriceCache,
    *,
    with_middleware: bool,
    now=None,
    settings=None,
) -> FastAPI:
    """Build a test app mirroring main.py's router set (sans chat/LLM).

    ``with_middleware=False`` yields the exact same app without the gateway —
    the byte-regression tests diff responses between the two stacks.
    ``settings`` (a RuntimeSettings) selects the runtime mode for the keys
    router and the gateway middleware; None keeps the local-demo default.
    """
    from app.routes.auth import create_auth_router
    from app.routes.backtest import create_backtest_router
    from app.routes.health import router as health_router
    from app.routes.keys import create_keys_router
    from app.routes.orders import create_orders_router
    from app.routes.portfolio import create_portfolio_router
    from app.routes.rules import create_rules_router
    from app.routes.strategies import create_strategies_router
    from app.routes.watchlist import create_watchlist_router

    test_app = FastAPI()
    test_app.state.market_source = FakeMarketSource(price_cache)
    test_app.include_router(health_router)
    test_app.include_router(create_stream_router(price_cache))
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_orders_router(price_cache, db_file))
    test_app.include_router(create_rules_router(price_cache, db_file))
    test_app.include_router(create_strategies_router(price_cache, db_file))
    test_app.include_router(create_backtest_router(price_cache))
    test_app.include_router(create_watchlist_router(price_cache, db_file))
    test_app.include_router(create_auth_router(db_file))
    test_app.include_router(create_keys_router(db_file, settings=settings))
    if with_middleware:
        kwargs = {"db_path": db_file}
        if now is not None:
            kwargs["now"] = now
        if settings is not None:
            kwargs["settings"] = settings
        test_app.add_middleware(ApiKeyGatewayMiddleware, **kwargs)
    return test_app


@pytest_asyncio.fixture
async def gateway_env(tmp_path, monkeypatch):
    """Gateway app + FakeTime + independent-cookie-jar client factory."""
    db_file = str(tmp_path / "gateway.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    fake_time = FakeTime()
    test_app = build_app(db_file, price_cache, with_middleware=True, now=fake_time)

    async with AsyncExitStack() as stack:

        async def make_client() -> AsyncClient:
            return await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
            )

        client = await make_client()
        yield SimpleNamespace(
            app=test_app,
            db_file=db_file,
            price_cache=price_cache,
            client=client,
            make_client=make_client,
            time=fake_time,
        )


async def login(client: AsyncClient, name: str) -> dict:
    """Log ``client``'s cookie jar in as ``name``; returns the user dict."""
    resp = await client.post("/api/auth/login", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["user"]


async def create_key(client: AsyncClient, label: str = "bot", **fields):
    """POST /api/keys; returns (plaintext, info). Asserts the 201 contract."""
    resp = await client.post("/api/keys", json={"label": label, **fields})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["key"], data["info"]


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def audit_rows(db_file: str, key_id: str | None = None) -> list:
    """All api_audit rows (oldest first), optionally scoped to one key."""
    conn = get_conn(db_file)
    try:
        if key_id is not None:
            return conn.execute(
                "SELECT * FROM api_audit WHERE key_id = ? ORDER BY created_at, rowid",
                (key_id,),
            ).fetchall()
        return conn.execute("SELECT * FROM api_audit ORDER BY created_at, rowid").fetchall()
    finally:
        conn.close()


def key_row(db_file: str, key_id: str):
    """The raw api_keys row (tests inspect key_hash / frozen directly)."""
    conn = get_conn(db_file)
    try:
        return conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    finally:
        conn.close()
