"""Tests for M4.1 lightweight identity — login, cookie session, anonymous default.

Covers the login validation matrix, new-user seeding ($10k cash + default
10-ticker watchlist), the cookie roundtrip through the httpx client jar,
/api/auth/me anonymous vs logged in, logout, and forged-signature fallback
to the anonymous 'default' user.
"""

from __future__ import annotations

import pytest

from app.auth import COOKIE_NAME
from app.market.seed_prices import DEFAULT_WATCHLIST


async def _login(client, name: str):
    return await client.post("/api/auth/login", json={"name": name})


class TestLoginValidation:
    """The login validation matrix: length, charset, reserved names."""

    async def test_name_too_short(self, app_client):
        resp = await _login(app_client, "a")
        assert resp.status_code == 400
        assert "2-24" in resp.json()["error"]

    async def test_name_too_long(self, app_client):
        resp = await _login(app_client, "a" * 25)
        assert resp.status_code == 400
        assert "2-24" in resp.json()["error"]

    async def test_boundary_lengths_accepted(self, app_client):
        assert (await _login(app_client, "ab")).status_code == 200
        assert (await _login(app_client, "b" * 24)).status_code == 200

    @pytest.mark.parametrize(
        "bad_name",
        ["bad name", "bob!", "a@b.com", "semi;colon", "dot.ted", "naïve", "tab\tname"],
    )
    async def test_bad_characters_rejected(self, app_client, bad_name):
        resp = await _login(app_client, bad_name)
        assert resp.status_code == 400
        assert "letters" in resp.json()["error"]

    async def test_allowed_characters_accepted(self, app_client):
        resp = await _login(app_client, "Trader_Bob-99")
        assert resp.status_code == 200
        assert resp.json()["user"]["id"] == "trader_bob-99"

    async def test_reserved_default_rejected(self, app_client):
        resp = await _login(app_client, "default")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Name is reserved"}

    async def test_reserved_is_case_insensitive(self, app_client):
        resp = await _login(app_client, "DeFaUlT")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Name is reserved"}

    async def test_name_is_stripped(self, app_client):
        resp = await _login(app_client, "  Alice  ")
        assert resp.status_code == 200
        assert resp.json()["user"] == {"id": "alice", "name": "Alice"}


class TestLoginNewUserSeeding:
    """New users get $10k cash, an empty book, and the default watchlist."""

    async def test_login_response_shape(self, app_client):
        resp = await _login(app_client, "Alice")
        assert resp.status_code == 200
        assert resp.json() == {"user": {"id": "alice", "name": "Alice"}}

    async def test_login_sets_session_cookie(self, app_client):
        resp = await _login(app_client, "Alice")
        cookie = resp.cookies.get(COOKIE_NAME)
        assert cookie is not None
        user_id, _, signature = cookie.rpartition(".")
        assert user_id == "alice"
        assert len(signature) == 64  # hmac-sha256 hexdigest
        set_cookie = resp.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie
        assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()

    async def test_new_user_starts_with_10k_and_no_positions(self, app_client):
        await _login(app_client, "Alice")
        portfolio = (await app_client.get("/api/portfolio/")).json()
        assert portfolio["cash"] == 10000.0
        assert portfolio["positions"] == []

    async def test_new_user_gets_default_watchlist(self, app_client):
        await _login(app_client, "Alice")
        watchlist = (await app_client.get("/api/watchlist/")).json()["tickers"]
        # All rows share one added_at timestamp, so response order is
        # unspecified — compare as sets.
        assert {t["ticker"] for t in watchlist} == set(DEFAULT_WATCHLIST)
        assert len(watchlist) == len(DEFAULT_WATCHLIST)

    async def test_existing_user_relogin_keeps_account(self, app_client):
        await _login(app_client, "alice")
        trade = await app_client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "side": "buy", "quantity": 5},
        )
        assert trade.status_code == 200

        # Re-login with different casing: same account, updated display name.
        resp = await _login(app_client, "ALICE")
        assert resp.json()["user"] == {"id": "alice", "name": "ALICE"}
        portfolio = (await app_client.get("/api/portfolio/")).json()
        assert len(portfolio["positions"]) == 1  # position survived re-login
        assert portfolio["cash"] < 10000.0  # cash NOT re-seeded
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "alice", "name": "ALICE"}


class TestMeAndLogout:
    """/api/auth/me resolution and logout cookie expiry."""

    async def test_me_anonymous_is_guest(self, app_client):
        resp = await app_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"user": {"id": "default", "name": "Guest"}}

    async def test_me_after_login(self, app_client):
        await _login(app_client, "Alice")
        resp = await app_client.get("/api/auth/me")
        assert resp.json() == {"user": {"id": "alice", "name": "Alice"}}

    async def test_logout_returns_ok_and_expires_cookie(self, app_client):
        await _login(app_client, "Alice")
        resp = await app_client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # The delete-cookie response empties the client jar — back to Guest.
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "default", "name": "Guest"}

    async def test_logout_without_session_is_idempotent(self, app_client):
        resp = await app_client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestSignatureVerification:
    """Tampered or malformed cookies always fall back to the anonymous user."""

    async def test_forged_signature_falls_back_to_default(self, app_client):
        await _login(app_client, "Alice")  # account exists
        app_client.cookies.set(COOKIE_NAME, "alice." + "0" * 64)
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "default", "name": "Guest"}
        # Scoped routes fall back too — alice's data is NOT reachable.
        watchlist = (await app_client.get("/api/watchlist/")).json()["tickers"]
        assert len(watchlist) == len(DEFAULT_WATCHLIST)  # guest's own list

    async def test_cookie_without_dot_falls_back_to_default(self, app_client):
        app_client.cookies.set(COOKIE_NAME, "garbagenodot")
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "default", "name": "Guest"}

    async def test_signature_for_other_user_rejected(self, app_client):
        """A valid cookie cannot be replayed for a different user id."""
        resp = await _login(app_client, "Alice")
        cookie = resp.cookies.get(COOKIE_NAME)
        _, _, alice_sig = cookie.rpartition(".")
        app_client.cookies.set(COOKIE_NAME, f"bob.{alice_sig}")
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "default", "name": "Guest"}

    async def test_valid_cookie_roundtrip(self, app_client):
        """The exact Set-Cookie value re-presented verifies back to the user."""
        resp = await _login(app_client, "Alice")
        cookie = resp.cookies.get(COOKIE_NAME)
        app_client.cookies.delete(COOKIE_NAME)
        app_client.cookies.set(COOKIE_NAME, cookie)
        me = (await app_client.get("/api/auth/me")).json()
        assert me["user"] == {"id": "alice", "name": "Alice"}
