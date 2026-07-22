"""Player public profile tests (P4 §4).

Covers GET /api/players/{user_id} (404, full public summary, the private
face for non-owners, owner always sees their own full summary with the
`public` flag reported as-is, ownership pinned to the COOKIE so the owner's
own Bearer key still gets the private face, leaderboard parity for
total/return/rank, weight math summing to ~100 with descending order, the
privacy face never leaking quantities/costs/cash, equity-curve downsampling
preserving the last point), PATCH /api/players/me (cookie identity happy
path, Bearer 403 red line, body validation, anonymous Guest), and the
users_profile.public_profile migration (fresh DB, legacy DB, idempotence,
default 1).
"""

from __future__ import annotations

import json
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api_gateway import ApiKeyGatewayMiddleware
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.auth import create_auth_router
from app.routes.keys import create_keys_router
from app.routes.leaderboard import create_leaderboard_router
from app.routes.players import (
    MAX_EQUITY_POINTS,
    create_players_router,
    downsample_equity_curve,
)

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def players_env(tmp_path, monkeypatch):
    """Players + auth + leaderboard routers over an isolated DB.

    ``make_client()`` returns an AsyncClient with its own cookie jar — one
    per simulated user, plus one for anonymous viewers.
    """
    db_file = str(tmp_path / "players.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_auth_router(db_file))
    test_app.include_router(create_players_router(price_cache, db_file))
    test_app.include_router(create_leaderboard_router(price_cache, db_file))

    async with AsyncExitStack() as stack:

        async def make_client() -> AsyncClient:
            return await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")
            )

        yield SimpleNamespace(
            db_file=db_file, price_cache=price_cache, make_client=make_client
        )


def _insert_position(db_file: str, user_id: str, ticker: str, quantity: float, avg_cost: float):
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, ticker, quantity, avg_cost, BASE_TIME.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_snapshots(db_file: str, user_id: str, values: list[float]):
    conn = get_conn(db_file)
    try:
        for i, value in enumerate(values):
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    value,
                    (BASE_TIME + timedelta(seconds=30 * i)).isoformat(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


async def _login(client: AsyncClient, name: str) -> str:
    resp = await client.post("/api/auth/login", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["user"]["id"]


@pytest.mark.asyncio
class TestGetPlayer:
    async def test_unknown_user_is_404(self, players_env):
        client = await players_env.make_client()
        resp = await client.get("/api/players/nobody")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Player not found"}

    async def test_public_summary_shape(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        _insert_position(players_env.db_file, "alice", "AAPL", 10, 150.0)
        _insert_snapshots(players_env.db_file, "alice", [10000.0, 10500.0])

        anon = await players_env.make_client()
        resp = await anon.get("/api/players/alice")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "user",
            "public",
            "profile_public",
            "total_value",
            "return_pct",
            "rank",
            "equity_curve",
            "positions_summary",
        }
        assert body["public"] is True
        assert body["profile_public"] is True  # actual stored flag
        assert set(body["user"]) == {"id", "name", "created_at"}
        assert body["user"]["id"] == "alice"
        assert body["user"]["name"] == "Alice"
        assert isinstance(body["rank"], int)
        assert len(body["equity_curve"]) == 2
        assert set(body["equity_curve"][0]) == {"time", "value"}
        assert body["equity_curve"][-1]["value"] == 10500.0

    async def test_totals_match_leaderboard(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        bob = await players_env.make_client()
        await _login(bob, "Bob")
        _insert_position(players_env.db_file, "alice", "AAPL", 10, 150.0)

        anon = await players_env.make_client()
        board = (await anon.get("/api/leaderboard")).json()["entries"]
        for user_id in ("alice", "bob"):
            entry = next(e for e in board if e["user_id"] == user_id)
            body = (await anon.get(f"/api/players/{user_id}")).json()
            assert body["total_value"] == entry["total_value"]
            assert body["return_pct"] == entry["return_pct"]
            assert body["rank"] == entry["rank"]

    async def test_private_profile_hides_summary_from_others(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        _insert_position(players_env.db_file, "alice", "AAPL", 10, 150.0)
        assert (await alice.patch("/api/players/me", json={"public": False})).json() == {
            "public": False
        }

        anon = await players_env.make_client()
        resp = await anon.get("/api/players/alice")
        assert resp.status_code == 200
        body = resp.json()
        # ONLY the identity + the flag — no totals, curve, or weights.
        assert body == {"user": {"id": "alice", "name": "Alice"}, "public": False}

    async def test_owner_sees_own_private_profile(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        _insert_position(players_env.db_file, "alice", "AAPL", 10, 150.0)
        await alice.patch("/api/players/me", json={"public": False})

        resp = await alice.get("/api/players/alice")
        assert resp.status_code == 200
        body = resp.json()
        # The owner still gets the FULL summary, but the flags report the
        # real stored state (public is never pinned true for the owner) —
        # the frontend privacy toggle reads them as its source of truth.
        assert body["public"] is False
        assert body["profile_public"] is False
        assert "equity_curve" in body
        assert "positions_summary" in body

    async def test_weights_sum_to_100_descending_without_leaking(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        # AAPL at seed ~190 x 10 dominates MSFT at seed ~420 x 1.
        _insert_position(players_env.db_file, "alice", "AAPL", 10, 150.0)
        _insert_position(players_env.db_file, "alice", "MSFT", 1, 400.0)

        anon = await players_env.make_client()
        body = (await anon.get("/api/players/alice")).json()
        summary = body["positions_summary"]
        assert [item["ticker"] for item in summary][0] == "AAPL"
        weights = [item["weight_pct"] for item in summary]
        assert weights == sorted(weights, reverse=True)
        assert abs(sum(weights) - 100.0) <= 0.2
        # Privacy face: weight percent only — never qty/cost/cash.
        for item in summary:
            assert set(item) == {"ticker", "weight_pct"}
        payload_text = json.dumps(body)
        for forbidden in ("quantity", "avg_cost", "cash_balance", "cash"):
            assert forbidden not in payload_text

    async def test_no_positions_yields_empty_summary(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        anon = await players_env.make_client()
        body = (await anon.get("/api/players/alice")).json()
        assert body["positions_summary"] == []

    async def test_equity_curve_downsampled_keeps_last_point(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        values = [10000.0 + i for i in range(700)]
        _insert_snapshots(players_env.db_file, "alice", values)

        anon = await players_env.make_client()
        body = (await anon.get("/api/players/alice")).json()
        curve = body["equity_curve"]
        assert len(curve) <= MAX_EQUITY_POINTS
        assert curve[0]["value"] == values[0]  # first point kept
        assert curve[-1]["value"] == values[-1]  # last point kept (保末点)
        last_iso = (BASE_TIME + timedelta(seconds=30 * 699)).timestamp()
        assert curve[-1]["time"] == int(last_iso)
        times = [p["time"] for p in curve]
        assert times == sorted(times)  # still ascending


@pytest.mark.asyncio
async def test_own_bearer_key_never_unlocks_private_profile(tmp_path, monkeypatch):
    """P4 §4 red line: the private-profile owner check is COOKIE-ONLY.

    A caller holding the owner's own VALID Bearer key goes through the real
    ``ApiKeyGatewayMiddleware`` (which injects ``request.state.api_user_id``
    == owner — the identity ``get_current_user_id`` would prefer), yet with
    ``public_profile = 0`` it must still get the ``{user, public: false}``
    private face with no curve/weights/totals: a leaked API key must never
    leak its owner's private summary.
    """
    db_file = str(tmp_path / "players_gateway.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_auth_router(db_file))
    test_app.include_router(create_keys_router(db_file))
    test_app.include_router(create_players_router(price_cache, db_file))
    test_app.add_middleware(ApiKeyGatewayMiddleware, db_path=db_file)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as alice:
        await _login(alice, "Alice")
        created = await alice.post("/api/keys", json={"label": "bot"})
        assert created.status_code == 201, created.text
        plaintext_key = created.json()["key"]

        _insert_position(db_file, "alice", "AAPL", 10, 150.0)
        _insert_snapshots(db_file, "alice", [10000.0, 10500.0])
        hidden = await alice.patch("/api/players/me", json={"public": False})
        assert hidden.status_code == 200

        # Fresh client: NO cookie jar — only Alice's own valid Bearer key.
        async with AsyncClient(transport=transport, base_url="http://test") as bearer:
            resp = await bearer.get(
                "/api/players/alice",
                headers={"Authorization": f"Bearer {plaintext_key}"},
            )
            assert resp.status_code == 200
            # Private face ONLY — no equity curve, weights, totals, or rank.
            assert resp.json() == {
                "user": {"id": "alice", "name": "Alice"},
                "public": False,
            }

        # Sanity: the owner's COOKIE still unlocks the full (private) summary.
        owner_view = (await alice.get("/api/players/alice")).json()
        assert owner_view["public"] is False
        assert "equity_curve" in owner_view


class TestDownsampleHelper:
    def test_under_limit_untouched(self):
        points = [{"time": i, "value": float(i)} for i in range(10)]
        assert downsample_equity_curve(points) == points

    def test_over_limit_uniform_keeps_ends(self):
        points = [{"time": i, "value": float(i)} for i in range(1234)]
        out = downsample_equity_curve(points)
        assert len(out) <= MAX_EQUITY_POINTS
        assert out[0] == points[0]
        assert out[-1] == points[-1]
        times = [p["time"] for p in out]
        assert times == sorted(set(times))  # unique, ascending


@pytest.mark.asyncio
class TestPatchPlayersMe:
    async def test_cookie_toggle_roundtrip(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")

        off = await alice.patch("/api/players/me", json={"public": False})
        assert off.status_code == 200
        assert off.json() == {"public": False}
        conn = get_conn(players_env.db_file)
        try:
            row = conn.execute(
                "SELECT public_profile FROM users_profile WHERE id = 'alice'"
            ).fetchone()
        finally:
            conn.close()
        assert row["public_profile"] == 0

        on = await alice.patch("/api/players/me", json={"public": True})
        assert on.json() == {"public": True}
        anon = await players_env.make_client()
        reopened = (await anon.get("/api/players/alice")).json()
        assert reopened["public"] is True
        assert reopened["profile_public"] is True

    async def test_bearer_call_is_403(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        # Cookie AND a Bearer header: the key-management red line wins — an
        # API key must never flip privacy even alongside a valid session.
        resp = await alice.patch(
            "/api/players/me",
            json={"public": False},
            headers={"Authorization": "Bearer fk_someleakedkey"},
        )
        assert resp.status_code == 403
        assert "error" in resp.json()
        # And the flag is untouched.
        anon = await players_env.make_client()
        assert (await anon.get("/api/players/alice")).json()["public"] is True

    async def test_invalid_bodies_are_400(self, players_env):
        alice = await players_env.make_client()
        await _login(alice, "Alice")
        for body in ({}, {"public": "yes"}, {"public": 1}, [True]):
            resp = await alice.patch("/api/players/me", json=body)
            assert resp.status_code == 400, body
            assert "error" in resp.json()

    async def test_anonymous_guest_toggles_default_row(self, players_env):
        anon = await players_env.make_client()
        resp = await anon.patch("/api/players/me", json={"public": False})
        assert resp.status_code == 200
        assert resp.json() == {"public": False}

        # An anonymous viewer IS the 'default' user (owner), so a LOGGED-IN
        # other user must be the one who sees the private face.
        bob = await players_env.make_client()
        await _login(bob, "Bob")
        body = (await bob.get("/api/players/default")).json()
        assert body == {"user": {"id": "default", "name": "Guest"}, "public": False}

        # ...while the anonymous Guest still sees their own full summary —
        # with both flags reporting the real (now-private) state.
        owner_view = (await anon.get("/api/players/default")).json()
        assert owner_view["public"] is False
        assert owner_view["profile_public"] is False
        assert "equity_curve" in owner_view


def _columns(db_file: str, table: str) -> set[str]:
    conn = get_conn(db_file)
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestPublicProfileMigration:
    def test_fresh_db_has_column_default_1(self, tmp_path):
        db = str(tmp_path / "fresh.db")
        init_db(db)
        assert "public_profile" in _columns(db, "users_profile")
        conn = get_conn(db)
        try:
            row = conn.execute(
                "SELECT public_profile FROM users_profile WHERE id = 'default'"
            ).fetchone()
        finally:
            conn.close()
        assert row["public_profile"] == 1

    def test_migration_adds_column_to_legacy_table(self, tmp_path):
        """Simulate a pre-P4 volume: users_profile WITHOUT public_profile."""
        db = str(tmp_path / "legacy.db")
        conn = get_conn(db)
        try:
            conn.execute(
                "CREATE TABLE users_profile (id TEXT PRIMARY KEY, "
                "cash_balance REAL NOT NULL DEFAULT 10000.0, "
                "created_at TEXT NOT NULL, display_name TEXT)"
            )
            conn.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at, display_name) "
                "VALUES ('veteran', 12345.0, '2025-01-01T00:00:00+00:00', 'Veteran')"
            )
            conn.commit()
        finally:
            conn.close()

        assert "public_profile" not in _columns(db, "users_profile")
        init_db(db)  # runs _migrate_schema
        assert "public_profile" in _columns(db, "users_profile")

        conn = get_conn(db)
        try:
            row = conn.execute(
                "SELECT cash_balance, public_profile FROM users_profile "
                "WHERE id = 'veteran'"
            ).fetchone()
        finally:
            conn.close()
        assert row["cash_balance"] == 12345.0
        assert row["public_profile"] == 1  # legacy rows default to public

    def test_migration_is_idempotent_and_preserves_toggle(self, tmp_path):
        db = str(tmp_path / "idem.db")
        init_db(db)
        conn = get_conn(db)
        try:
            conn.execute(
                "UPDATE users_profile SET public_profile = 0 WHERE id = 'default'"
            )
            conn.commit()
        finally:
            conn.close()

        init_db(db)  # second run: no raise, no duplicate column, value kept
        conn = get_conn(db)
        try:
            row = conn.execute(
                "SELECT public_profile FROM users_profile WHERE id = 'default'"
            ).fetchone()
        finally:
            conn.close()
        assert row["public_profile"] == 0
