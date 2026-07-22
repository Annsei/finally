"""Tests for /api/competitions (D2 §3) — timed private competitions.

Existing test files (conftest.py included) are frozen, so this module builds
its own arena-style fixture: one app over an isolated temp SQLite DB with the
auth, portfolio, keys and competitions routers plus the API-key gateway
middleware (FakeTime clock), and a ``make_client()`` factory returning
independent cookie jars — one per simulated user.

Covered (contract §6): create shape/validation/limit-of-5, the
compute_standings-caliber baseline, join idempotency + ended 400 + unknown
404, board ranking with joined_at tie-breaks, ended boards reading the last
portfolio_snapshot at-or-before ends_at (baseline fallback), the Bearer
matrix (create 403 / join 200 — bots may enter deliberately), and cross-user
visibility (mine vs all scope, creator-only code).
"""

from __future__ import annotations

import re
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api_gateway import ApiKeyGatewayMiddleware
from app.db.connection import get_conn, init_db
from app.market import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.routes.auth import create_auth_router
from app.routes.competitions import (
    CODE_ALPHABET,
    MAX_RUNNING_PER_USER,
    create_competitions_router,
)
from app.routes.keys import create_keys_router
from app.routes.portfolio import create_portfolio_router
from tests.conftest import FakeTime

# The E2E spec pins this exact shape (6 chars, A-Z2-9).
CODE_RE = re.compile(r"^[A-Z2-9]{6}$")

SUMMARY_KEYS = {
    "id", "name", "code", "status", "member_count", "starts_at", "ends_at",
}
BOARD_KEYS = {"user_id", "name", "baseline_value", "value", "return_pct", "rank"}


@pytest_asyncio.fixture
async def comp_env(tmp_path, monkeypatch):
    """Competitions app + gateway middleware + independent-cookie clients."""
    db_file = str(tmp_path / "competitions.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    for ticker, price in SEED_PRICES.items():
        price_cache.update(ticker, price)

    test_app = FastAPI()
    test_app.include_router(create_portfolio_router(price_cache, db_file))
    test_app.include_router(create_auth_router(db_file))
    test_app.include_router(create_keys_router(db_file))
    test_app.include_router(create_competitions_router(price_cache, db_file))
    fake_time = FakeTime()
    test_app.add_middleware(ApiKeyGatewayMiddleware, db_path=db_file, now=fake_time)

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


async def _login(client: AsyncClient, name: str) -> dict:
    resp = await client.post("/api/auth/login", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["user"]


async def _create(client: AsyncClient, name: str = "Race", hours: int = 24) -> dict:
    resp = await client.post("/api/competitions", json={"name": name, "hours": hours})
    assert resp.status_code == 201, resp.text
    return resp.json()["competition"]


async def _mint_key(client: AsyncClient, label: str = "bot") -> str:
    resp = await client.post("/api/keys", json={"label": label})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _member_rows(db_file: str, competition_id: str) -> list:
    conn = get_conn(db_file)
    try:
        return conn.execute(
            "SELECT * FROM competition_members WHERE competition_id = ? "
            "ORDER BY joined_at",
            (competition_id,),
        ).fetchall()
    finally:
        conn.close()


def _end_competition(db_file: str, competition_id: str, hours_ago: float = 1.0) -> str:
    """Rewind a competition into the past; returns the new ends_at."""
    now = datetime.now(timezone.utc)
    starts = (now - timedelta(hours=hours_ago + 1)).isoformat()
    ends = (now - timedelta(hours=hours_ago)).isoformat()
    conn = get_conn(db_file)
    try:
        conn.execute(
            "UPDATE competitions SET starts_at = ?, ends_at = ? WHERE id = ?",
            (starts, ends, competition_id),
        )
        conn.commit()
    finally:
        conn.close()
    return ends


def _insert_snapshot(db_file: str, user_id: str, total_value: float, recorded_at: str) -> None:
    conn = get_conn(db_file)
    try:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, total_value, recorded_at),
        )
        conn.commit()
    finally:
        conn.close()


class TestCreate:
    """POST /api/competitions — cookie-only creation with auto-join."""

    async def test_create_shape_code_and_duration(self, comp_env):
        anon = comp_env.client  # the anonymous Guest can create (local demo)
        resp = await anon.post(
            "/api/competitions", json={"name": "  Friday Race  ", "hours": 24}
        )
        assert resp.status_code == 201
        comp = resp.json()["competition"]

        assert set(comp.keys()) == SUMMARY_KEYS
        assert comp["name"] == "Friday Race"  # trimmed
        assert comp["status"] == "running"  # starts_at = created_at (born running)
        assert comp["member_count"] == 1
        assert CODE_RE.fullmatch(comp["code"])
        assert set(comp["code"]) <= set(CODE_ALPHABET)  # no I/O/0/1 confusables

        starts = datetime.fromisoformat(comp["starts_at"])
        ends = datetime.fromisoformat(comp["ends_at"])
        assert (ends - starts) == timedelta(hours=24)

        # Creator auto-joined with the fresh-account baseline.
        members = _member_rows(comp_env.db_file, comp["id"])
        assert len(members) == 1
        assert members[0]["user_id"] == "default"
        assert members[0]["baseline_value"] == 10000.0

    async def test_baseline_uses_compute_standings_caliber(self, comp_env):
        alice = await comp_env.make_client()
        await _login(alice, "Alice")

        comp_env.price_cache.update("AAPL", 100.0)
        resp = await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 10}
        )
        assert resp.status_code == 200
        comp_env.price_cache.update("AAPL", 150.0)  # 9000 cash + 10*150 = 10500

        comp = await _create(alice, name="Caliber")
        members = _member_rows(comp_env.db_file, comp["id"])
        assert members[0]["user_id"] == "alice"
        assert members[0]["baseline_value"] == 10500.0

    async def test_validation_matrix(self, comp_env):
        anon = comp_env.client
        bad_payloads = [
            {},  # both missing
            {"name": "", "hours": 24},
            {"name": "   ", "hours": 24},
            {"name": "x" * 41, "hours": 24},
            {"name": 42, "hours": 24},
            {"name": "ok", "hours": 0},
            {"name": "ok", "hours": 169},
            {"name": "ok", "hours": 24.5},
            {"name": "ok", "hours": "24"},
            {"name": "ok", "hours": True},
            {"name": "ok"},  # hours missing
        ]
        for payload in bad_payloads:
            resp = await anon.post("/api/competitions", json=payload)
            assert resp.status_code == 400, payload
            assert "error" in resp.json()

        # Non-object / unparsable bodies are 400 too.
        resp = await anon.post("/api/competitions", json=[1, 2])
        assert resp.status_code == 400
        resp = await anon.post(
            "/api/competitions",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

        # Boundary values are accepted.
        assert (await _create(anon, name="x", hours=1))["status"] == "running"
        assert (await _create(anon, name="y" * 40, hours=168))["status"] == "running"

    async def test_running_limit_of_five_per_creator(self, comp_env):
        alice = await comp_env.make_client()
        await _login(alice, "Alice")

        created = [
            await _create(alice, name=f"Race {i}") for i in range(MAX_RUNNING_PER_USER)
        ]
        resp = await alice.post(
            "/api/competitions", json={"name": "one too many", "hours": 24}
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "error": f"Competition limit reached ({MAX_RUNNING_PER_USER} running per user)"
        }

        # Another user is unaffected by alice's limit.
        bob = await comp_env.make_client()
        await _login(bob, "Bob")
        await _create(bob, name="Bob race")

        # An ENDED competition no longer counts against the limit.
        _end_competition(comp_env.db_file, created[0]["id"])
        assert (await _create(alice, name="after one ended"))["status"] == "running"


class TestJoin:
    """POST /api/competitions/join — running-only, idempotent, Bearer OK."""

    async def test_join_adds_member_and_counts(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        comp = await _create(alice)
        resp = await bob.post("/api/competitions/join", json={"code": comp["code"]})
        assert resp.status_code == 200
        joined = resp.json()["competition"]
        assert joined["id"] == comp["id"]
        assert joined["member_count"] == 2
        assert joined["code"] is None  # bob is not the creator

        members = _member_rows(comp_env.db_file, comp["id"])
        assert [m["user_id"] for m in members] == ["alice", "bob"]

    async def test_repeat_join_is_idempotent_and_keeps_baseline(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice)

        first = await bob.post("/api/competitions/join", json={"code": comp["code"]})
        assert first.status_code == 200
        baseline_before = _member_rows(comp_env.db_file, comp["id"])[1]["baseline_value"]
        assert baseline_before == 10000.0

        # Bob's live value moves; a repeat join must NOT rewrite the baseline.
        comp_env.price_cache.update("AAPL", 100.0)
        assert (
            await bob.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "side": "buy", "quantity": 10},
            )
        ).status_code == 200
        comp_env.price_cache.update("AAPL", 200.0)

        again = await bob.post("/api/competitions/join", json={"code": comp["code"]})
        assert again.status_code == 200
        assert again.json()["competition"]["member_count"] == 2
        members = _member_rows(comp_env.db_file, comp["id"])
        assert len(members) == 2
        assert members[1]["baseline_value"] == baseline_before

    async def test_join_code_is_normalized(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice)

        resp = await bob.post(
            "/api/competitions/join", json={"code": f"  {comp['code'].lower()}  "}
        )
        assert resp.status_code == 200
        assert resp.json()["competition"]["member_count"] == 2

    async def test_join_unknown_code_404(self, comp_env):
        resp = await comp_env.client.post(
            "/api/competitions/join", json={"code": "ZZZZZZ"}
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "Competition not found"}

    async def test_join_ended_competition_400(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice)
        _end_competition(comp_env.db_file, comp["id"])

        resp = await bob.post("/api/competitions/join", json={"code": comp["code"]})
        assert resp.status_code == 400
        assert resp.json() == {"error": "Competition has ended"}

    async def test_join_body_validation(self, comp_env):
        anon = comp_env.client
        for payload in ({}, {"code": ""}, {"code": "   "}, {"code": 123}):
            resp = await anon.post("/api/competitions/join", json=payload)
            assert resp.status_code == 400, payload
        resp = await anon.post(
            "/api/competitions/join",
            content=b"junk",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


class TestBearerMatrix:
    """Creation is cookie-only (403); join/read allow Bearer (deliberate)."""

    async def test_bearer_create_403_via_gateway(self, comp_env):
        alice = await comp_env.make_client()
        await _login(alice, "Alice")
        key = await _mint_key(alice)

        resp = await comp_env.client.post(
            "/api/competitions",
            json={"name": "bot race", "hours": 24},
            headers=_bearer(key),
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "API keys cannot create competitions"}

        conn = get_conn(comp_env.db_file)
        try:
            assert conn.execute("SELECT COUNT(*) FROM competitions").fetchone()[0] == 0
        finally:
            conn.close()

    async def test_raw_bearer_header_403_without_middleware(self, comp_env):
        """Belt and braces (keys.py pattern): the in-route header check holds
        even in an app that mounts the router without the gateway."""
        bare_app = FastAPI()
        bare_app.include_router(
            create_competitions_router(comp_env.price_cache, comp_env.db_file)
        )
        async with AsyncClient(
            transport=ASGITransport(app=bare_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/competitions",
                json={"name": "bot race", "hours": 24},
                headers={"Authorization": "Bearer not-a-real-key"},
            )
            assert resp.status_code == 403
            assert resp.json() == {"error": "API keys cannot create competitions"}

    async def test_bearer_join_200_as_key_owner(self, comp_env):
        """Bots may sign themselves up (contract §3 — deliberate design)."""
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice)
        bob_key = await _mint_key(bob)

        resp = await comp_env.client.post(
            "/api/competitions/join",
            json={"code": comp["code"]},
            headers=_bearer(bob_key),
        )
        assert resp.status_code == 200
        assert resp.json()["competition"]["member_count"] == 2
        members = _member_rows(comp_env.db_file, comp["id"])
        assert [m["user_id"] for m in members] == ["alice", "bob"]

        # Bearer reads work too: the key owner's mine scope and the board.
        listed = await comp_env.client.get(
            "/api/competitions", headers=_bearer(bob_key)
        )
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["competitions"]] == [comp["id"]]
        detail = await comp_env.client.get(
            f"/api/competitions/{comp['id']}", headers=_bearer(bob_key)
        )
        assert detail.status_code == 200
        assert len(detail.json()["board"]) == 2


class TestListScopes:
    """GET /api/competitions?scope=mine|all — membership and code visibility."""

    async def test_mine_is_membership_scoped_and_code_creator_only(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice, name="Visible")

        # Default scope is mine: bob is not a member yet.
        assert (await bob.get("/api/competitions")).json()["competitions"] == []

        # scope=all reveals the row to everyone — but never the code.
        all_rows = (await bob.get("/api/competitions?scope=all")).json()["competitions"]
        assert [c["id"] for c in all_rows] == [comp["id"]]
        assert all_rows[0]["code"] is None
        assert all_rows[0]["member_count"] == 1

        # After joining, the competition lands in bob's mine scope — code
        # still hidden (仅 mine 且本人创建).
        await bob.post("/api/competitions/join", json={"code": comp["code"]})
        bob_mine = (await bob.get("/api/competitions?scope=mine")).json()["competitions"]
        assert [c["id"] for c in bob_mine] == [comp["id"]]
        assert bob_mine[0]["code"] is None
        assert bob_mine[0]["member_count"] == 2

        # The creator's mine row carries the code.
        alice_mine = (await alice.get("/api/competitions")).json()["competitions"]
        assert alice_mine[0]["code"] == comp["code"]

        # Creator rows in scope=all hide the code too (mine-only reveal).
        alice_all = (await alice.get("/api/competitions?scope=all")).json()["competitions"]
        assert alice_all[0]["code"] is None

    async def test_invalid_scope_400(self, comp_env):
        resp = await comp_env.client.get("/api/competitions?scope=everything")
        assert resp.status_code == 400
        assert resp.json() == {"error": "scope must be 'mine' or 'all'"}

    async def test_list_is_newest_first_with_status(self, comp_env):
        alice = await comp_env.make_client()
        await _login(alice, "Alice")
        first = await _create(alice, name="first")
        second = await _create(alice, name="second")
        _end_competition(comp_env.db_file, first["id"])

        rows = (await alice.get("/api/competitions")).json()["competitions"]
        assert [c["id"] for c in rows] == [second["id"], first["id"]]
        assert [c["status"] for c in rows] == ["running", "ended"]
        for row in rows:
            assert set(row.keys()) == SUMMARY_KEYS


class TestBoard:
    """GET /api/competitions/{id} — ranked board, running and ended."""

    async def test_unknown_competition_404(self, comp_env):
        resp = await comp_env.client.get("/api/competitions/nope")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Competition not found"}

    async def test_non_member_can_view_board(self, comp_env):
        """契约 §3：查看对任意身份开放——未加入且非创建者也能看 board。"""
        alice, carol = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(carol, "Carol")
        comp = await _create(alice, name="Open viewing")

        resp = await carol.get(f"/api/competitions/{comp['id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["code"] is None  # join code stays creator-only
        assert all(m["user_id"] != "carol" for m in detail["board"])

    async def test_naive_timestamp_degrades_to_ended(self, comp_env):
        """手改 DB 的 naive 时间戳降级为 ended，绝不 500。"""
        alice = await comp_env.make_client()
        await _login(alice, "Alice")
        comp = await _create(alice, name="Naive times")
        conn = get_conn(comp_env.db_file)
        try:
            conn.execute(
                "UPDATE competitions SET starts_at = ?, ends_at = ? WHERE id = ?",
                ("2026-07-12T10:00:00", "2026-07-12T12:00:00", comp["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        resp = await alice.get(f"/api/competitions/{comp['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"

    async def test_running_board_ranks_by_return_pct(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        comp_env.price_cache.update("AAPL", 100.0)
        assert (
            await alice.post(
                "/api/portfolio/trade",
                json={"ticker": "AAPL", "side": "buy", "quantity": 10},
            )
        ).status_code == 200

        comp = await _create(alice, name="Live board")  # alice baseline 10000
        await bob.post("/api/competitions/join", json={"code": comp["code"]})

        comp_env.price_cache.update("AAPL", 150.0)  # alice: 9000 + 1500 = 10500
        detail = (await bob.get(f"/api/competitions/{comp['id']}")).json()

        assert detail["id"] == comp["id"]
        assert detail["status"] == "running"
        assert detail["member_count"] == 2
        assert detail["code"] is None  # bob is not the creator

        board = detail["board"]
        assert [set(row.keys()) for row in board] == [BOARD_KEYS, BOARD_KEYS]
        assert [row["rank"] for row in board] == [1, 2]
        assert board[0]["user_id"] == "alice"
        assert board[0]["name"] == "Alice"
        assert board[0]["baseline_value"] == 10000.0
        assert board[0]["value"] == 10500.0
        assert board[0]["return_pct"] == 5.0
        assert board[1]["user_id"] == "bob"
        assert board[1]["value"] == 10000.0
        assert board[1]["return_pct"] == 0.0

        # The creator sees the code on the detail view.
        assert (await alice.get(f"/api/competitions/{comp['id']}")).json()[
            "code"
        ] == comp["code"]

    async def test_tied_returns_rank_by_joined_at(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice, name="Tie")
        await bob.post("/api/competitions/join", json={"code": comp["code"]})

        board = (await alice.get(f"/api/competitions/{comp['id']}")).json()["board"]
        assert [row["return_pct"] for row in board] == [0.0, 0.0]
        # Both flat at 0% — the earlier joined_at (the creator) wins the tie.
        assert [row["user_id"] for row in board] == ["alice", "bob"]
        assert [row["rank"] for row in board] == [1, 2]

    async def test_ended_board_reads_last_snapshot_before_ends_at(self, comp_env):
        alice, bob = [await comp_env.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        comp = await _create(alice, name="Final")
        await bob.post("/api/competitions/join", json={"code": comp["code"]})

        ends_iso = _end_competition(comp_env.db_file, comp["id"], hours_ago=1.0)
        ends = datetime.fromisoformat(ends_iso)

        # Alice: two snapshots inside the run and one AFTER ends_at that
        # must be ignored; live prices must be ignored as well.
        _insert_snapshot(
            comp_env.db_file, "alice", 11000.0,
            (ends - timedelta(minutes=40)).isoformat(),
        )
        _insert_snapshot(
            comp_env.db_file, "alice", 12000.0,
            (ends - timedelta(minutes=10)).isoformat(),
        )
        _insert_snapshot(
            comp_env.db_file, "alice", 99999.0,
            (ends + timedelta(minutes=10)).isoformat(),
        )

        detail = (await bob.get(f"/api/competitions/{comp['id']}")).json()
        assert detail["status"] == "ended"
        board = detail["board"]

        assert board[0]["user_id"] == "alice"
        assert board[0]["value"] == 12000.0  # last snapshot at/before ends_at
        assert board[0]["return_pct"] == 20.0
        assert board[0]["rank"] == 1

        # Bob has NO snapshot in the window → baseline fallback, 0% return.
        assert board[1]["user_id"] == "bob"
        assert board[1]["value"] == 10000.0
        assert board[1]["baseline_value"] == 10000.0
        assert board[1]["return_pct"] == 0.0
        assert board[1]["rank"] == 2

    async def test_guest_membership_and_board_name(self, comp_env):
        """The anonymous Guest can create/join; the board names it Guest."""
        comp = await _create(comp_env.client, name="Guest race")
        board = (
            await comp_env.client.get(f"/api/competitions/{comp['id']}")
        ).json()["board"]
        assert board == [
            {
                "user_id": "default",
                "name": "Guest",
                "baseline_value": 10000.0,
                "value": 10000.0,
                "return_pct": 0.0,
                "rank": 1,
            }
        ]
