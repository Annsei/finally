"""Auth API routes for FinAlly (M4.1) — name-only login, cookie session.

Provides:
- POST /api/auth/login  — {"name": str} → upsert user, seed new users
  ($10k cash + default 10-ticker watchlist), set the ``finally_session``
  cookie. 200 {"user": {"id", "name"}}.
- POST /api/auth/logout — expire the cookie. 200 {"ok": true}.
- GET  /api/auth/me     — current user; anonymous resolves to
  {"user": {"id": "default", "name": "Guest"}}.

Name rules: strip; 2-24 chars; [A-Za-z0-9_-]+ only. The user id is the
lowercased name (case-insensitive identity; display keeps the original
casing, updated on every login). 'default' is reserved for the anonymous
Guest user and rejected with "Name is reserved".

Created via the factory ``create_auth_router`` closing over ``db_path``,
mirroring the other routers.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    get_current_user_id,
    session_cookie_value,
)
from app.db.connection import get_conn
from app.market.seed_prices import DEFAULT_WATCHLIST
from app.routes.watchlist import sync_market_source

logger = logging.getLogger(__name__)

NAME_MIN_LEN = 2
NAME_MAX_LEN = 24
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
RESERVED_USER_IDS = {"default"}


class LoginRequest(BaseModel):
    name: str


def _set_session_cookie(response: JSONResponse, user_id: str, db_path: str) -> None:
    """Attach the signed 30-day session cookie (HttpOnly, SameSite=Lax, path=/)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_cookie_value(user_id, db_path),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def create_auth_router(db_path: str) -> APIRouter:
    """Factory: build the auth APIRouter with the injected database path."""
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    async def login(body: LoginRequest, request: Request) -> JSONResponse:
        """Log in (creating the account on first use) and set the session cookie.

        New users start with $10,000 cash and the default 10-ticker
        watchlist. An existing user logging in again just refreshes the
        session (display-name casing is updated to the latest login).
        """
        name = body.name.strip()
        if not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
            return JSONResponse(
                status_code=400,
                content={"error": "Name must be 2-24 characters"},
            )
        if not _NAME_PATTERN.fullmatch(name):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Name may only contain letters, digits, hyphens, and underscores"
                },
            )
        user_id = name.lower()
        if user_id in RESERVED_USER_IDS:
            return JSONResponse(status_code=400, content={"error": "Name is reserved"})

        now = datetime.now(timezone.utc).isoformat()
        is_new_user = False
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
            if row is None:
                is_new_user = True
                conn.execute(
                    "INSERT INTO users_profile (id, cash_balance, created_at, display_name) "
                    "VALUES (?, 10000.0, ?, ?)",
                    (user_id, now, name),
                )
                for ticker in DEFAULT_WATCHLIST:
                    conn.execute(
                        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                        "VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4()), user_id, ticker, now),
                    )
            else:
                conn.execute(
                    "UPDATE users_profile SET display_name = ? WHERE id = ?",
                    (name, user_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if is_new_user:
            # Best-effort: make sure the new user's seeded tickers stream —
            # another user may have removed some from the shared source.
            # add_ticker is idempotent; failures are logged in the helper and
            # the source re-syncs from the DB union on the next startup.
            for ticker in DEFAULT_WATCHLIST:
                await sync_market_source(request, ticker, "add")
            logger.info("New user %r created (display name %r)", user_id, name)

        response = JSONResponse(content={"user": {"id": user_id, "name": name}})
        _set_session_cookie(response, user_id, db_path)
        return response

    @router.post("/logout")
    async def logout() -> JSONResponse:
        """Expire the session cookie. Always succeeds (idempotent)."""
        response = JSONResponse(content={"ok": True})
        response.delete_cookie(key=COOKIE_NAME, path="/")
        return response

    @router.get("/me")
    async def me(request: Request) -> dict:
        """Return the current user; anonymous requests resolve to Guest."""
        user_id = get_current_user_id(request, db_path)
        if user_id == "default":
            return {"user": {"id": "default", "name": "Guest"}}

        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT display_name FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            # Valid signature but the profile vanished (e.g. hand-edited DB):
            # treat as anonymous rather than invent an account.
            return {"user": {"id": "default", "name": "Guest"}}
        return {"user": {"id": user_id, "name": row["display_name"] or user_id}}

    return router
