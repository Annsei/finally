"""Arena player public profile routes for FinAlly (P4 §4).

Endpoints (factory ``create_players_router`` — mirrors the other routers):

- GET ``/api/players/{user_id}`` (no auth) — the player's PUBLIC summary:

    {"user": {"id", "name", "created_at"}, "public": bool,
     "profile_public": bool,
     "total_value": float, "return_pct": float, "rank": int | null,
     "equity_curve": [{"time": unix_seconds, "value": float}, ...],
     "positions_summary": [{"ticker": str, "weight_pct": float 1dp}, ...]}

  Privacy face (core invariant): ONLY the summary is exposed — the equity
  curve and position weights, NEVER quantities, average costs, or the cash
  balance. ``total_value`` / ``return_pct`` / ``rank`` reuse the
  ``leaderboard.compute_standings`` math verbatim so the player page and the
  leaderboard always agree. The equity curve reads ``portfolio_snapshots``
  ascending and uniformly downsamples above 500 points, always keeping the
  last point. Unknown users are 404; a user with ``public_profile = 0`` is
  ``{"user": {"id", "name"}, "public": false}`` for everyone but the owner —
  the owner always sees their own full summary.

  Ownership is COOKIE-ONLY (contract §4: cookie 判定). The resolver here
  (``_cookie_viewer_id``) deliberately ignores the gateway-injected
  ``request.state.api_user_id``: a caller holding the user's own valid
  Bearer API key must still get the private face — a leaked key must never
  unlock its owner's private profile.

  ``public`` carries the ACTUAL stored ``public_profile`` flag — an owner
  viewing their own PRIVATE profile still receives the full payload, just
  with ``public: false``. ``profile_public`` duplicates the flag on the
  full shape (the frontend toggle reads it, falling back to ``public``).

- PATCH ``/api/players/me`` (cookie identity ONLY) — ``{"public": bool}`` ->
  ``{"public": bool}``. Bearer-authenticated calls get 403, aligned with the
  P3 key-management red line: a leaked API key must never be able to flip
  its owner's privacy face.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import COOKIE_NAME, get_current_user_id, sign_user_id
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.leaderboard import STARTING_CASH, _display_name, compute_standings

logger = logging.getLogger(__name__)

# P4 §4: equity curves above this many snapshots are uniformly downsampled
# (last point always preserved).
MAX_EQUITY_POINTS = 500


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _bearer_rejection(request: Request) -> JSONResponse | None:
    """403 for any Bearer-authenticated call (P3 red-line parity).

    Same belt-and-braces shape as /api/keys: a gateway-resolved key id on
    request.state OR a raw Authorization: Bearer header (a router mounted
    without the middleware) both reject — API keys never manage privacy.
    """
    if getattr(request.state, "api_key_id", None) is not None:
        return _error(403, "Keys cannot manage player privacy")
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        return _error(403, "Keys cannot manage player privacy")
    return None


def _cookie_viewer_id(request: Request, db_path: str) -> str:
    """Resolve the viewer from the ``finally_session`` cookie ONLY.

    Same verification as ``auth.get_current_user_id`` (via the shared
    ``sign_user_id`` primitive) but WITHOUT its Bearer short-circuit: the
    gateway-injected ``request.state.api_user_id`` is deliberately ignored,
    because the private-profile owner check is pinned to the cookie identity
    (contract §4) — an API key must never unlock its owner's private page.
    Falls back to ``'default'`` (anonymous Guest) exactly like the shared
    resolver; never raises.
    """
    cookies = getattr(request, "cookies", None)
    raw = cookies.get(COOKIE_NAME) if cookies else None
    if not raw or "." not in raw:
        return "default"
    user_id, _, signature = raw.rpartition(".")
    if not user_id or not signature:
        return "default"
    if not hmac.compare_digest(sign_user_id(user_id, db_path), signature):
        return "default"
    return user_id


def _iso_to_unix(value: str) -> int:
    """Unix seconds for an ISO-8601 timestamp (naive values read as UTC)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def downsample_equity_curve(points: list[dict], limit: int = MAX_EQUITY_POINTS) -> list[dict]:
    """Uniformly downsample an equity curve to at most ``limit`` points.

    Index selection is ``round(i * (n-1) / (limit-1))`` for i in 0..limit-1,
    so the FIRST and LAST points are always preserved (P4 §4: 保末点) and the
    rest spread evenly. At or under the limit the list is returned as-is.
    """
    n = len(points)
    if n <= limit:
        return points
    step = (n - 1) / (limit - 1)
    indices = sorted({round(i * step) for i in range(limit)})
    return [points[i] for i in indices]


def create_players_router(
    price_cache: PriceCache, db_path: str, seed_cash: float = STARTING_CASH
) -> APIRouter:
    """Factory: build the players APIRouter with injected dependencies.

    ``seed_cash`` is the return-percent baseline handed to
    ``compute_standings`` (CN-1: main.py injects the active market profile's
    seed cash, so /player return% always matches the leaderboard's).
    """
    router = APIRouter(prefix="/api/players", tags=["players"])

    @router.patch("/me")
    async def update_my_privacy(request: Request):
        """Flip the caller's privacy toggle. Cookie identity ONLY (P4 §4)."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection

        try:
            payload = await request.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("public"), bool):
            return _error(400, "public must be a boolean")
        public = payload["public"]

        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            updated = conn.execute(
                "UPDATE users_profile SET public_profile = ? WHERE id = ?",
                (1 if public else 0, user_id),
            ).rowcount
            conn.commit()
        finally:
            conn.close()
        if updated == 0:
            return _error(404, "Player not found")
        logger.info("Player %r set public_profile=%s", user_id, public)
        return {"public": public}

    @router.get("/{user_id}")
    async def get_player(user_id: str, request: Request):
        """Return a player's public summary (P4 §4). No auth required.

        - Unknown user -> 404 ``{"error": "Player not found"}``.
        - ``public_profile = 0`` and the viewer is not the owner ->
          ``{"user": {"id", "name"}, "public": false}`` — nothing else.
          The owner check is COOKIE-ONLY (``_cookie_viewer_id``): a Bearer
          key resolving to the owner must still get this private face.
        - Otherwise the full summary (see module docstring), with ``public``
          / ``profile_public`` both carrying the actual stored flag so the
          owner's toggle has a source of truth. The payload NEVER carries
          quantities, average costs, or cash.
        """
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT id, cash_balance, created_at, display_name, public_profile "
                "FROM users_profile WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return _error(404, "Player not found")
            name = _display_name(row)

            viewer_id = _cookie_viewer_id(request, db_path)
            if not row["public_profile"] and viewer_id != user_id:
                return {"user": {"id": user_id, "name": name}, "public": False}

            # Leaderboard math, verbatim (P4 §4: total/return/rank 口径复用).
            standings = compute_standings(conn, price_cache, seed_cash)
            entry = next((e for e in standings if e["user_id"] == user_id), None)

            snapshot_rows = conn.execute(
                "SELECT total_value, recorded_at FROM portfolio_snapshots "
                "WHERE user_id = ? ORDER BY recorded_at ASC, rowid ASC",
                (user_id,),
            ).fetchall()
            equity_curve = downsample_equity_curve(
                [
                    {
                        "time": _iso_to_unix(s["recorded_at"]),
                        "value": round(s["total_value"], 2),
                    }
                    for s in snapshot_rows
                ]
            )

            # Position weights by CURRENT market value (uncached tickers value
            # at cost — same fallback as compute_standings). Weights are the
            # share of the invested (position) value, so they sum to ~100.
            # Quantities and costs feed the math but NEVER the payload.
            position_rows = conn.execute(
                "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            values: list[tuple[str, float]] = []
            for p in position_rows:
                price = price_cache.get_price(p["ticker"])
                if price is None:
                    price = p["avg_cost"]
                values.append((p["ticker"], p["quantity"] * price))
            invested = sum(v for _, v in values)
            positions_summary = (
                sorted(
                    (
                        {"ticker": ticker, "weight_pct": round(v / invested * 100.0, 1)}
                        for ticker, v in values
                    ),
                    key=lambda item: (-item["weight_pct"], item["ticker"]),
                )
                if invested > 0
                else []
            )
        finally:
            conn.close()

        return {
            "user": {"id": user_id, "name": name, "created_at": row["created_at"]},
            # The ACTUAL stored flag, as-is: the owner viewing their own
            # PRIVATE profile gets the full payload with public: false.
            "public": bool(row["public_profile"]),
            # Duplicate of `public` on the full shape — the frontend privacy
            # toggle reads this (with a `public` fallback); kept so older
            # clients of the P4 payload keep working.
            "profile_public": bool(row["public_profile"]),
            "total_value": entry["total_value"] if entry else None,
            "return_pct": entry["return_pct"] if entry else None,
            "rank": entry["rank"] if entry else None,
            "equity_curve": equity_curve,
            "positions_summary": positions_summary,
        }

    return router
