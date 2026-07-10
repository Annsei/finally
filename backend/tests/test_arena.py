"""Tests for the M4 multi-user arena: isolation, leaderboard, and seasons.

Uses the ``arena`` fixture (conftest): one app, several independent httpx
clients (one cookie jar each) simulating logged-in users plus the anonymous
Guest. Background-loop behavior (fill loop, snapshot task) is driven
synchronously via the loops' unit-testable pass functions.
"""

from __future__ import annotations

from app.db.connection import get_conn
from app.routes.orders import process_open_orders_once
from app.routes.portfolio import record_snapshots_for_all_users


async def _login(client, name: str) -> dict:
    resp = await client.post("/api/auth/login", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["user"]


async def _portfolio(client) -> dict:
    return (await client.get("/api/portfolio/")).json()


class TestUserIsolation:
    """User A's trades/watchlist/rules/chat are invisible to B and to Guest."""

    async def test_trade_is_invisible_to_other_users(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        resp = await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 5}
        )
        assert resp.status_code == 200

        alice_pf = await _portfolio(alice)
        assert len(alice_pf["positions"]) == 1
        assert alice_pf["cash"] < 10000.0

        for other in (bob, anon):
            pf = await _portfolio(other)
            assert pf["positions"] == []
            assert pf["cash"] == 10000.0

        # Blotter is scoped too.
        assert (await alice.get("/api/portfolio/trades")).json()["trades"] != []
        assert (await bob.get("/api/portfolio/trades")).json()["trades"] == []
        assert (await anon.get("/api/portfolio/trades")).json()["trades"] == []

    async def test_watchlist_add_is_invisible_to_other_users(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        assert (await alice.post("/api/watchlist/", json={"ticker": "PYPL"})).status_code == 200

        alice_tickers = [
            t["ticker"] for t in (await alice.get("/api/watchlist/")).json()["tickers"]
        ]
        assert "PYPL" in alice_tickers
        for other in (bob, anon):
            tickers = [
                t["ticker"] for t in (await other.get("/api/watchlist/")).json()["tickers"]
            ]
            assert "PYPL" not in tickers

    async def test_rule_is_invisible_to_other_users(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        resp = await alice.post(
            "/api/rules",
            json={
                "ticker": "NVDA", "trigger_type": "price_above",
                "threshold": 99999.0, "side": "buy", "quantity": 1,
            },
        )
        assert resp.status_code == 200
        rule_id = resp.json()["rule"]["id"]

        assert len((await alice.get("/api/rules")).json()["rules"]) == 1
        assert (await bob.get("/api/rules")).json()["rules"] == []
        assert (await anon.get("/api/rules")).json()["rules"] == []

        # Cross-user mutation is a 404 (ids are not disclosed across users).
        assert (await bob.delete(f"/api/rules/{rule_id}")).status_code == 404
        assert (
            await bob.patch(f"/api/rules/{rule_id}", json={"status": "paused"})
        ).status_code == 404
        assert len((await alice.get("/api/rules")).json()["rules"]) == 1

    async def test_order_cancel_is_scoped_to_owner(self, arena):
        alice, bob = [await arena.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        resp = await alice.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": 1,
                  "kind": "limit", "limit_price": 1.0},
        )
        assert resp.status_code == 200
        order_id = resp.json()["order"]["id"]

        assert (await bob.get("/api/portfolio/orders")).json()["orders"] == []
        assert (await bob.delete(f"/api/portfolio/orders/{order_id}")).status_code == 404
        # Still open for alice.
        alice_orders = (await alice.get("/api/portfolio/orders")).json()["orders"]
        assert alice_orders[0]["status"] == "open"

    async def test_chat_history_and_actions_are_scoped(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        resp = await alice.post("/api/chat/", json={"message": "buy some apple"})
        assert resp.status_code == 200
        # LLM_MOCK executes: buy 5 AAPL + add PYPL — on ALICE's account.
        assert resp.json()["trades"][0]["status"] == "executed"

        assert len((await alice.get("/api/chat/")).json()["messages"]) == 2
        assert (await bob.get("/api/chat/")).json()["messages"] == []
        assert (await anon.get("/api/chat/")).json()["messages"] == []

        # The mock's actions landed on alice only.
        assert len((await _portfolio(alice))["positions"]) == 1
        assert (await _portfolio(bob))["positions"] == []
        bob_tickers = [
            t["ticker"] for t in (await bob.get("/api/watchlist/")).json()["tickers"]
        ]
        assert "PYPL" not in bob_tickers

    async def test_fill_loop_executes_order_on_owners_portfolio_only(self, arena):
        alice, bob = [await arena.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        arena.price_cache.update("AAPL", 150.0)
        resp = await alice.post(
            "/api/portfolio/orders",
            json={"ticker": "AAPL", "side": "buy", "quantity": 5,
                  "kind": "limit", "limit_price": 100.0},
        )
        assert resp.status_code == 200
        assert resp.json()["order"]["status"] == "open"  # rests below market

        arena.price_cache.update("AAPL", 99.0)  # now marketable
        counts = process_open_orders_once(arena.db_file, arena.price_cache)
        assert counts["filled"] == 1

        alice_pf = await _portfolio(alice)
        assert alice_pf["cash"] == 10000.0 - 5 * 99.0
        assert alice_pf["positions"][0]["ticker"] == "AAPL"
        alice_order = (await alice.get("/api/portfolio/orders")).json()["orders"][0]
        assert alice_order["status"] == "filled"
        assert alice_order["fill_price"] == 99.0

        bob_pf = await _portfolio(bob)
        assert bob_pf["cash"] == 10000.0
        assert bob_pf["positions"] == []
        assert (await bob.get("/api/portfolio/orders")).json()["orders"] == []

    async def test_snapshot_task_writes_one_row_per_user(self, arena):
        alice, bob = [await arena.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        conn = get_conn(arena.db_file)
        try:
            count = record_snapshots_for_all_users(conn, arena.price_cache)
            conn.commit()
            rows = conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM portfolio_snapshots GROUP BY user_id"
            ).fetchall()
        finally:
            conn.close()

        assert count == 3  # default + alice + bob
        assert {row["user_id"]: row["n"] for row in rows} == {
            "default": 1, "alice": 1, "bob": 1,
        }

        # Each user sees only their own history.
        assert len((await alice.get("/api/portfolio/history")).json()["snapshots"]) == 1
        assert len((await bob.get("/api/portfolio/history")).json()["snapshots"]) == 1


class TestMarketSourceUnion:
    """The market source tracks the union of ALL users' watchlists (M4)."""

    async def test_ticker_removed_from_source_only_when_unwatched_by_all(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        # AAPL is watched by default (guest), alice, and bob.
        await alice.delete("/api/watchlist/AAPL")
        assert "AAPL" not in arena.market_source.removed  # bob + guest watch it
        assert "AAPL" in arena.market_source.get_tickers()

        await bob.delete("/api/watchlist/AAPL")
        assert "AAPL" not in arena.market_source.removed  # guest still watches

        await anon.delete("/api/watchlist/AAPL")
        assert "AAPL" in arena.market_source.removed  # nobody watches now
        assert "AAPL" not in arena.market_source.get_tickers()

    async def test_new_user_login_restores_seeded_tickers_to_source(self, arena):
        alice, bob, anon = [await arena.make_client() for _ in range(3)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")
        for client in (alice, bob, anon):
            await client.delete("/api/watchlist/AAPL")
        assert "AAPL" not in arena.market_source.get_tickers()

        carol = await arena.make_client()
        await _login(carol, "Carol")  # new-user seeding re-adds the defaults
        assert "AAPL" in arena.market_source.get_tickers()

    async def test_chat_removal_respects_other_watchers(self, arena):
        """Chat-driven removal only drops the source when nobody watches."""
        alice = await arena.make_client()
        await _login(alice, "Alice")

        # Guest and alice both watch MSFT; alice removes hers via the
        # watchlist route — the source must keep streaming for the guest.
        await alice.delete("/api/watchlist/MSFT")
        assert "MSFT" not in arena.market_source.removed
        assert "MSFT" in arena.market_source.get_tickers()


class TestLeaderboard:
    """GET /api/leaderboard — ranking and return math across every user."""

    async def test_shape_ranking_and_return_math(self, arena):
        alice, bob = [await arena.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        arena.price_cache.update("AAPL", 100.0)
        resp = await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 10}
        )
        assert resp.status_code == 200
        arena.price_cache.update("AAPL", 150.0)  # alice: 9000 + 10*150 = 10500

        board = (await bob.get("/api/leaderboard")).json()
        assert board["season"]["id"] == 1
        assert board["season"]["started_at"]

        entries = board["entries"]
        assert [e["rank"] for e in entries] == [1, 2, 3]

        assert entries[0]["user_id"] == "alice"
        assert entries[0]["name"] == "Alice"
        assert entries[0]["total_value"] == 10500.0
        assert entries[0]["return_pct"] == 5.0

        # Tie at $10k: the guest profile was created first (at init) so it
        # ranks ahead of bob (earlier created_at wins ties).
        assert entries[1]["user_id"] == "default"
        assert entries[1]["name"] == "Guest"
        assert entries[1]["total_value"] == 10000.0
        assert entries[1]["return_pct"] == 0.0
        assert entries[2]["user_id"] == "bob"

    async def test_uncached_ticker_falls_back_to_avg_cost(self, arena):
        alice = await arena.make_client()
        await _login(alice, "Alice")

        arena.price_cache.update("AAPL", 100.0)
        await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 10}
        )
        arena.price_cache.remove("AAPL")  # no quote — value at avg cost

        board = (await alice.get("/api/leaderboard")).json()
        alice_entry = next(e for e in board["entries"] if e["user_id"] == "alice")
        assert alice_entry["total_value"] == 10000.0
        assert alice_entry["return_pct"] == 0.0

    async def test_negative_return_rounding(self, arena):
        alice = await arena.make_client()
        await _login(alice, "Alice")

        arena.price_cache.update("AAPL", 100.0)
        await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 10}
        )
        arena.price_cache.update("AAPL", 66.67)  # 9000 + 666.70 = 9666.70

        board = (await alice.get("/api/leaderboard")).json()
        alice_entry = next(e for e in board["entries"] if e["user_id"] == "alice")
        assert alice_entry["total_value"] == 9666.70
        assert alice_entry["return_pct"] == -3.33  # (9666.70-10000)/10000*100


class TestSeasonReset:
    """POST /api/season/reset and GET /api/seasons (M4.3)."""

    @staticmethod
    def _headers(request_id: str | None = None) -> dict[str, str]:
        headers = {"x-finally-admin-token": "local-demo-admin"}
        if request_id is not None:
            headers["idempotency-key"] = request_id
        return headers

    async def test_confirm_gate(self, arena):
        anon = await arena.make_client()
        for payload in ({}, {"confirm": False}):
            resp = await anon.post(
                "/api/season/reset", json=payload, headers=self._headers()
            )
            assert resp.status_code == 400
            assert resp.json() == {"error": "Confirmation required"}
        # Missing body entirely is also rejected.
        resp = await anon.post("/api/season/reset", headers=self._headers())
        assert resp.status_code == 400
        assert resp.json() == {"error": "Confirmation required"}
        # Nothing was archived or reset.
        seasons = (await anon.get("/api/seasons")).json()["seasons"]
        assert len(seasons) == 1
        assert seasons[0]["ended_at"] is None

    async def test_local_demo_token_optional_but_validated(self, arena):
        # local-demo does not require the admin token (classroom-server does —
        # see test_runtime_hardening), but a supplied token is still checked,
        # and the Idempotency-Key header is mandatory in every mode.
        anon = await arena.make_client()
        wrong = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers={
                "x-finally-admin-token": "wrong",
                "idempotency-key": "wrong-token",
            },
        )
        assert wrong.status_code == 403
        no_key = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers=self._headers(),
        )
        assert no_key.status_code == 400

    async def test_local_demo_reset_succeeds_without_admin_token(self, arena):
        anon = await arena.make_client()
        resp = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers={"idempotency-key": "local-demo-no-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["season"]["id"] == 2

    async def test_reset_retry_is_idempotent_and_audited_once(self, arena):
        anon = await arena.make_client()
        headers = self._headers("same-reset")
        first = await anon.post(
            "/api/season/reset", json={"confirm": True}, headers=headers
        )
        second = await anon.post(
            "/api/season/reset", json={"confirm": True}, headers=headers
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        conn = get_conn(arena.db_file)
        try:
            assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0] == 1
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM seasons WHERE ended_at IS NULL"
                ).fetchone()[0]
                == 1
            )
        finally:
            conn.close()

    async def test_reset_pauses_and_clears_live_strategy_state(self, arena):
        conn = get_conn(arena.db_file)
        try:
            conn.execute(
                "INSERT INTO strategies "
                "(id, user_id, name, ticker, status, entry, exits, sizing, "
                "created_at, open_qty, open_price, opened_at, high_water, cooldown_until) "
                "VALUES ('live-1', 'default', 'live', 'AAPL', 'live', '{}', '{}', "
                "'{}', 'now', 5, 100, 'now', 110, 123)"
            )
            conn.commit()
        finally:
            conn.close()
        anon = await arena.make_client()
        resp = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers=self._headers("strategy-reset"),
        )
        assert resp.status_code == 200
        conn = get_conn(arena.db_file)
        try:
            row = conn.execute(
                "SELECT status, open_qty, open_price, opened_at, high_water, "
                "cooldown_until FROM strategies WHERE id = 'live-1'"
            ).fetchone()
        finally:
            conn.close()
        assert dict(row) == {
            "status": "paused",
            "open_qty": 0.0,
            "open_price": None,
            "opened_at": None,
            "high_water": None,
            "cooldown_until": None,
        }

    async def test_reset_archives_standings_and_resets_everyone(self, arena):
        alice, bob = [await arena.make_client() for _ in range(2)]
        await _login(alice, "Alice")
        await _login(bob, "Bob")

        # Alice: position worth +5%; an open resting order; an active rule;
        # a chat row (kind='review' — the mock review writes no trades).
        arena.price_cache.update("AAPL", 100.0)
        await alice.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy", "quantity": 10}
        )
        arena.price_cache.update("AAPL", 150.0)
        order = (await alice.post(
            "/api/portfolio/orders",
            json={"ticker": "MSFT", "side": "buy", "quantity": 1,
                  "kind": "limit", "limit_price": 1.0},
        )).json()["order"]
        rule = (await alice.post(
            "/api/rules",
            json={"ticker": "NVDA", "trigger_type": "price_above",
                  "threshold": 99999.0, "side": "buy", "quantity": 1},
        )).json()["rule"]
        assert (await alice.post("/api/chat/review")).status_code == 200

        resp = await bob.post(
            "/api/season/reset",
            json={"confirm": True},
            headers=self._headers("archive-season-1"),
        )
        assert resp.status_code == 200
        body = resp.json()

        # New current season.
        assert body["season"]["id"] == 2
        assert body["season"]["ended_at"] is None
        assert body["season"]["started_at"]

        # Archived standings for season 1.
        archived = body["archived"]
        assert archived["season_id"] == 1
        entries = archived["entries"]
        assert [e["rank"] for e in entries] == [1, 2, 3]
        assert entries[0]["user_id"] == "alice"
        assert entries[0]["final_value"] == 10500.0
        assert entries[0]["return_pct"] == 5.0
        assert {e["user_id"] for e in entries} == {"alice", "bob", "default"}

        # Everyone reset: $10k, flat book.
        for client in (alice, bob):
            pf = await _portfolio(client)
            assert pf["cash"] == 10000.0
            assert pf["positions"] == []

        # Open order cancelled; active rule paused.
        orders = (await alice.get("/api/portfolio/orders")).json()["orders"]
        assert {o["id"]: o["status"] for o in orders}[order["id"]] == "cancelled"
        rules = (await alice.get("/api/rules")).json()["rules"]
        assert {r["id"]: r["status"] for r in rules}[rule["id"]] == "paused"

        # History preserved: trades and chat survive the reset.
        assert len((await alice.get("/api/portfolio/trades")).json()["trades"]) == 1
        kinds = [m["kind"] for m in (await alice.get("/api/chat/")).json()["messages"]]
        assert "review" in kinds

        # Fresh leaderboard on season 2: everyone back to zero.
        board = (await bob.get("/api/leaderboard")).json()
        assert board["season"]["id"] == 2
        assert all(e["total_value"] == 10000.0 for e in board["entries"])
        assert all(e["return_pct"] == 0.0 for e in board["entries"])

    async def test_get_seasons_shape(self, arena):
        alice = await arena.make_client()
        await _login(alice, "Alice")
        assert (
            await alice.post(
                "/api/season/reset",
                json={"confirm": True},
                headers=self._headers("shape-season-1"),
            )
        ).status_code == 200

        seasons = (await alice.get("/api/seasons")).json()["seasons"]
        assert len(seasons) == 2

        # Newest first: season 2 is current (no results yet).
        assert seasons[0]["id"] == 2
        assert seasons[0]["ended_at"] is None
        assert seasons[0]["results"] is None

        # Season 1 is archived with full results sorted by rank.
        assert seasons[1]["id"] == 1
        assert seasons[1]["ended_at"] is not None
        results = seasons[1]["results"]
        assert [r["rank"] for r in results] == [1, 2]  # default + alice
        for r in results:
            assert set(r) == {"user_id", "name", "final_value", "return_pct", "rank"}

    async def test_second_reset_archives_season_two(self, arena):
        anon = await arena.make_client()
        first = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers=self._headers("first-reset"),
        )
        assert first.json()["season"]["id"] == 2
        second = await anon.post(
            "/api/season/reset",
            json={"confirm": True},
            headers=self._headers("second-reset"),
        )
        assert second.json()["season"]["id"] == 3
        assert second.json()["archived"]["season_id"] == 2

        seasons = (await anon.get("/api/seasons")).json()["seasons"]
        assert [s["id"] for s in seasons] == [3, 2, 1]
        assert seasons[0]["results"] is None
        assert seasons[1]["results"] is not None
        assert seasons[2]["results"] is not None
