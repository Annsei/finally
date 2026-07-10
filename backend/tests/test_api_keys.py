"""P3 §6 — /api/keys management API: create / list / patch / delete / audit page.

Covers the creation contract (plaintext exactly once, sha256-only storage,
11-char prefix, per-user limit of 10), the validation matrix, PATCH null
semantics, cross-user 404s, revocation keeping audit rows, and the audit
pagination endpoint (limit clamp 1..200, before cursor, has_more).
"""

# The gateway_env fixture is imported from tests.gateway_fixtures (conftest is
# frozen for P3); every test parameter named gateway_env "shadows" that import.
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.api_gateway import utc_now_iso, write_audit
from app.db.connection import get_conn, init_db
from app.market import PriceCache

# Imported fixtures/helpers (pytest picks fixtures up from this namespace).
from tests.gateway_fixtures import (  # noqa: F401
    audit_rows,
    bearer,
    build_app,
    create_key,
    gateway_env,
    key_row,
    login,
    server_settings,
)

INFO_FIELDS = {
    "id",
    "label",
    "prefix",
    "created_at",
    "last_used_at",
    "frozen",
    "allowed_tickers",
    "max_order_qty",
    "daily_trade_cap",
}


class TestCreateKey:
    async def test_create_returns_201_with_plaintext_and_info(self, gateway_env):
        await login(gateway_env.client, "alice")
        resp = await gateway_env.client.post("/api/keys", json={"label": "my bot"})
        assert resp.status_code == 201
        data = resp.json()
        assert set(data.keys()) == {"key", "info"}
        assert data["key"].startswith("fk_")
        assert len(data["key"]) > 30  # fk_ + token_urlsafe(32)
        info = data["info"]
        assert set(info.keys()) == INFO_FIELDS
        assert info["label"] == "my bot"
        assert info["frozen"] is False
        assert info["last_used_at"] is None
        assert info["allowed_tickers"] is None
        assert info["max_order_qty"] is None
        assert info["daily_trade_cap"] is None

    async def test_prefix_is_first_11_chars_of_plaintext(self, gateway_env):
        await login(gateway_env.client, "alice")
        key, info = await create_key(gateway_env.client)
        assert info["prefix"] == key[:11]
        assert len(info["prefix"]) == 11

    async def test_only_sha256_hash_stored_never_plaintext(self, gateway_env):
        await login(gateway_env.client, "alice")
        key, info = await create_key(gateway_env.client)
        row = key_row(gateway_env.db_file, info["id"])
        assert row["key_hash"] == hashlib.sha256(key.encode()).hexdigest()
        # No column of the stored row contains the plaintext.
        for column in row.keys():
            value = row[column]
            if isinstance(value, str) and column != "prefix":
                assert key not in value

    async def test_plaintext_and_hash_absent_from_list_response(self, gateway_env):
        await login(gateway_env.client, "alice")
        key, info = await create_key(gateway_env.client)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        resp = await gateway_env.client.get("/api/keys")
        assert resp.status_code == 200
        body = resp.text
        assert key not in body
        assert key_hash not in body
        assert info["prefix"] in body  # the display prefix IS listed

    async def test_constraints_stored_and_tickers_uppercased(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(
            gateway_env.client,
            label="scoped",
            allowed_tickers=["aapl", " msft "],
            max_order_qty=5,
            daily_trade_cap=3,
        )
        assert info["allowed_tickers"] == ["AAPL", "MSFT"]
        assert info["max_order_qty"] == 5.0
        assert info["daily_trade_cap"] == 3

    async def test_empty_allowed_tickers_means_unrestricted(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client, allowed_tickers=[])
        assert info["allowed_tickers"] is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},  # label missing
            {"label": ""},
            {"label": "   "},
            {"label": "x" * 41},
            {"label": 42},
        ],
    )
    async def test_label_validation_400(self, gateway_env, payload):
        await login(gateway_env.client, "alice")
        resp = await gateway_env.client.post("/api/keys", json=payload)
        assert resp.status_code == 400
        assert "Label" in resp.json()["error"]

    async def test_label_boundary_lengths_accepted(self, gateway_env):
        await login(gateway_env.client, "alice")
        assert (await gateway_env.client.post("/api/keys", json={"label": "x"})).status_code == 201
        assert (
            await gateway_env.client.post("/api/keys", json={"label": "y" * 40})
        ).status_code == 201

    @pytest.mark.parametrize(
        "payload,fragment",
        [
            ({"allowed_tickers": "AAPL"}, "allowed_tickers"),
            ({"allowed_tickers": [1, 2]}, "allowed_tickers"),
            ({"allowed_tickers": [""]}, "allowed_tickers"),
            ({"max_order_qty": 0}, "max_order_qty"),
            ({"max_order_qty": -5}, "max_order_qty"),
            ({"max_order_qty": "ten"}, "max_order_qty"),
            ({"max_order_qty": True}, "max_order_qty"),
            ({"daily_trade_cap": 0}, "daily_trade_cap"),
            ({"daily_trade_cap": 1.5}, "daily_trade_cap"),
            ({"daily_trade_cap": "many"}, "daily_trade_cap"),
        ],
    )
    async def test_constraint_validation_400(self, gateway_env, payload, fragment):
        await login(gateway_env.client, "alice")
        resp = await gateway_env.client.post("/api/keys", json={"label": "k", **payload})
        assert resp.status_code == 400
        assert fragment in resp.json()["error"]

    async def test_non_finite_max_order_quantity_rejected(self, gateway_env):
        await login(gateway_env.client, "alice")
        resp = await gateway_env.client.post(
            "/api/keys",
            content=b'{"label":"k","max_order_qty":Infinity}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert "max_order_qty" in resp.json()["error"]

    async def test_overflowing_max_order_quantity_rejected(self, gateway_env):
        await login(gateway_env.client, "alice")
        resp = await gateway_env.client.post(
            "/api/keys",
            json={"label": "k", "max_order_qty": 10**400},
        )
        assert resp.status_code == 400
        assert "max_order_qty" in resp.json()["error"]

    async def test_ten_keys_per_user_limit(self, gateway_env):
        await login(gateway_env.client, "alice")
        for i in range(10):
            await create_key(gateway_env.client, label=f"key-{i}")
        resp = await gateway_env.client.post("/api/keys", json={"label": "eleventh"})
        assert resp.status_code == 400
        assert "limit" in resp.json()["error"].lower()

    async def test_limit_is_per_user_not_global(self, gateway_env):
        alice = gateway_env.client
        await login(alice, "alice")
        for i in range(10):
            await create_key(alice, label=f"key-{i}")
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        resp = await bob.post("/api/keys", json={"label": "bobs"})
        assert resp.status_code == 201

    async def test_guest_can_create_key(self, gateway_env):
        # Anonymous (no cookie) resolves to 'default' — single-user mode works.
        # local-demo keeps the P3 contract; server mode is tested below.
        resp = await gateway_env.client.post("/api/keys", json={"label": "guest bot"})
        assert resp.status_code == 201
        row = key_row(gateway_env.db_file, resp.json()["info"]["id"])
        assert row["user_id"] == "default"

    async def test_guest_cannot_create_key_in_server_mode(self, tmp_path, monkeypatch):
        # classroom-server: the anonymous Guest is a shared identity and must
        # not own durable credentials — creation requires a named login.
        db_file = str(tmp_path / "server-keys.db")
        monkeypatch.setenv("DB_PATH", db_file)
        init_db(db_file)
        test_app = build_app(
            db_file, PriceCache(), with_middleware=True, settings=server_settings()
        )
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/keys", json={"label": "guest bot"})
        assert resp.status_code == 403
        assert resp.json() == {"error": "Guest users cannot create API keys"}

    async def test_invalid_json_body_400(self, gateway_env):
        resp = await gateway_env.client.post(
            "/api/keys", content=b"not json", headers={"content-type": "application/json"}
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid JSON body"}


class TestListKeys:
    async def test_list_scoped_to_owner(self, gateway_env):
        alice = gateway_env.client
        await login(alice, "alice")
        _, alice_info = await create_key(alice, label="alices")
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        await create_key(bob, label="bobs")

        alice_keys = (await alice.get("/api/keys")).json()["keys"]
        assert [k["id"] for k in alice_keys] == [alice_info["id"]]
        bob_keys = (await bob.get("/api/keys")).json()["keys"]
        assert [k["label"] for k in bob_keys] == ["bobs"]

    async def test_list_has_no_hash_field(self, gateway_env):
        await login(gateway_env.client, "alice")
        await create_key(gateway_env.client)
        keys = (await gateway_env.client.get("/api/keys")).json()["keys"]
        assert set(keys[0].keys()) == INFO_FIELDS


class TestPatchKey:
    async def test_patch_label_and_frozen(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client, label="old")
        resp = await gateway_env.client.patch(
            f"/api/keys/{info['id']}", json={"label": "new", "frozen": True}
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["label"] == "new"
        assert updated["frozen"] is True
        # Unfreeze again.
        resp = await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"frozen": False})
        assert resp.json()["frozen"] is False

    async def test_patch_explicit_null_clears_constraints(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(
            gateway_env.client,
            allowed_tickers=["AAPL"],
            max_order_qty=5,
            daily_trade_cap=2,
        )
        resp = await gateway_env.client.patch(
            f"/api/keys/{info['id']}",
            json={"allowed_tickers": None, "max_order_qty": None, "daily_trade_cap": None},
        )
        updated = resp.json()
        assert updated["allowed_tickers"] is None
        assert updated["max_order_qty"] is None
        assert updated["daily_trade_cap"] is None

    async def test_patch_absent_fields_unchanged(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(
            gateway_env.client, allowed_tickers=["AAPL"], max_order_qty=5
        )
        resp = await gateway_env.client.patch(f"/api/keys/{info['id']}", json={"label": "renamed"})
        updated = resp.json()
        assert updated["allowed_tickers"] == ["AAPL"]
        assert updated["max_order_qty"] == 5.0

    @pytest.mark.parametrize(
        "payload",
        [
            {"label": ""},
            {"frozen": "yes"},
            {"frozen": 1},
            {"allowed_tickers": "AAPL"},
            {"max_order_qty": -1},
            {"daily_trade_cap": 0},
        ],
    )
    async def test_patch_validation_400(self, gateway_env, payload):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        resp = await gateway_env.client.patch(f"/api/keys/{info['id']}", json=payload)
        assert resp.status_code == 400

    async def test_patch_cross_user_and_unknown_404(self, gateway_env):
        alice = gateway_env.client
        await login(alice, "alice")
        _, info = await create_key(alice)
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        resp = await bob.patch(f"/api/keys/{info['id']}", json={"frozen": True})
        assert resp.status_code == 404
        resp = await alice.patch(f"/api/keys/{uuid.uuid4()}", json={"frozen": True})
        assert resp.status_code == 404
        # Alice's key is untouched by Bob's attempt.
        assert key_row(gateway_env.db_file, info["id"])["frozen"] == 0


class TestDeleteKey:
    async def test_delete_removes_key(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        resp = await gateway_env.client.delete(f"/api/keys/{info['id']}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert key_row(gateway_env.db_file, info["id"]) is None
        # Second delete: gone → 404.
        resp = await gateway_env.client.delete(f"/api/keys/{info['id']}")
        assert resp.status_code == 404

    async def test_delete_cross_user_404(self, gateway_env):
        alice = gateway_env.client
        await login(alice, "alice")
        _, info = await create_key(alice)
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        resp = await bob.delete(f"/api/keys/{info['id']}")
        assert resp.status_code == 404
        assert key_row(gateway_env.db_file, info["id"]) is not None

    async def test_delete_keeps_audit_rows(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        write_audit(
            gateway_env.db_file,
            key_id=info["id"],
            user_id="alice",
            method="POST",
            endpoint="/api/portfolio/trade",
            result="ok",
            status_code=200,
        )
        await gateway_env.client.delete(f"/api/keys/{info['id']}")
        assert len(audit_rows(gateway_env.db_file, info["id"])) == 1


class TestAuditPagination:
    def _seed_audit(self, db_file: str, key_id: str, count: int) -> list[str]:
        """Insert ``count`` audit rows with strictly increasing created_at."""
        conn = get_conn(db_file)
        try:
            stamps = []
            for i in range(count):
                stamp = f"2026-07-08T00:00:{i:02d}.{i:06d}+00:00"
                stamps.append(stamp)
                conn.execute(
                    "INSERT INTO api_audit (id, key_id, user_id, method, endpoint, "
                    "payload_digest, result, status_code, created_at) "
                    "VALUES (?, ?, 'alice', 'POST', '/api/portfolio/trade', ?, 'ok', 200, ?)",
                    (str(uuid.uuid4()), key_id, f'{{"n":{i}}}', stamp),
                )
            conn.commit()
            return stamps
        finally:
            conn.close()

    async def test_default_limit_50_newest_first(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        stamps = self._seed_audit(gateway_env.db_file, info["id"], 55)
        resp = await gateway_env.client.get(f"/api/keys/{info['id']}/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 50
        assert data["has_more"] is True
        assert data["entries"][0]["created_at"] == stamps[-1]  # newest first
        created = [e["created_at"] for e in data["entries"]]
        assert created == sorted(created, reverse=True)

    async def test_before_cursor_pages_through(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        self._seed_audit(gateway_env.db_file, info["id"], 5)
        page1 = (
            await gateway_env.client.get(f"/api/keys/{info['id']}/audit", params={"limit": 3})
        ).json()
        assert len(page1["entries"]) == 3
        assert page1["has_more"] is True
        cursor = page1["entries"][-1]["created_at"]
        page2 = (
            await gateway_env.client.get(
                f"/api/keys/{info['id']}/audit", params={"limit": 3, "before": cursor}
            )
        ).json()
        assert len(page2["entries"]) == 2
        assert page2["has_more"] is False
        # No overlap, full coverage.
        ids = {e["id"] for e in page1["entries"]} | {e["id"] for e in page2["entries"]}
        assert len(ids) == 5

    async def test_limit_clamped_to_1_200(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        self._seed_audit(gateway_env.db_file, info["id"], 3)
        low = (
            await gateway_env.client.get(f"/api/keys/{info['id']}/audit", params={"limit": 0})
        ).json()
        assert len(low["entries"]) == 1
        high = await gateway_env.client.get(
            f"/api/keys/{info['id']}/audit", params={"limit": 9999}
        )
        assert high.status_code == 200  # clamped to 200, not rejected
        bad = await gateway_env.client.get(
            f"/api/keys/{info['id']}/audit", params={"limit": "abc"}
        )
        assert bad.status_code == 400

    async def test_audit_cross_user_404(self, gateway_env):
        alice = gateway_env.client
        await login(alice, "alice")
        _, info = await create_key(alice)
        bob = await gateway_env.make_client()
        await login(bob, "bob")
        resp = await bob.get(f"/api/keys/{info['id']}/audit")
        assert resp.status_code == 404

    async def test_entries_never_contain_key_material(self, gateway_env):
        await login(gateway_env.client, "alice")
        key, info = await create_key(gateway_env.client)
        write_audit(
            gateway_env.db_file,
            key_id=info["id"],
            user_id="alice",
            method="POST",
            endpoint="/api/portfolio/trade",
            result="ok",
            status_code=200,
            digest=json.dumps({"ticker": "AAPL", "quantity": 1}),
        )
        body = (await gateway_env.client.get(f"/api/keys/{info['id']}/audit")).text
        assert key not in body
        assert hashlib.sha256(key.encode()).hexdigest() not in body


class TestKeyTimestamp:
    async def test_created_at_is_utc_iso(self, gateway_env):
        await login(gateway_env.client, "alice")
        _, info = await create_key(gateway_env.client)
        # Same format family as the rest of the app (comparable to utc_now_iso).
        assert info["created_at"] <= utc_now_iso()
        assert info["created_at"].endswith("+00:00")
