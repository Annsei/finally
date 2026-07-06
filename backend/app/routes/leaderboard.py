"""Leaderboard API route for FinAlly (M4.2).

Provides:
- GET /api/leaderboard — current-season standings for EVERY user profile:

    {"season": {"id": int, "started_at": iso},
     "entries": [{"user_id", "name", "total_value", "return_pct", "rank"}]}

Standings math (contract fixed — frontend built in parallel):
- ``total_value`` = cash + Σ quantity × live cache price, falling back to the
  position's ``avg_cost`` when the ticker has no cached quote; rounded 2dp.
- ``return_pct`` = (total_value − 10000) / 10000 × 100, rounded 2dp (every
  user — including season resets — starts from $10,000).
- ``rank``: total_value descending; ties break to the earlier
  ``created_at`` (then user id, for full determinism). Entries are sorted by
  rank.

``compute_standings`` is shared with the seasons endpoints (M4.3), which
archive exactly these standings on reset.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.connection import get_conn
from app.market.cache import PriceCache

logger = logging.getLogger(__name__)

# Every user starts (and restarts each season) with $10,000.
STARTING_CASH = 10000.0


def _display_name(row: sqlite3.Row) -> str:
    """Public name for a profile row (Guest for the anonymous default user)."""
    if row["display_name"]:
        return row["display_name"]
    return "Guest" if row["id"] == "default" else row["id"]


def compute_standings(conn: sqlite3.Connection, price_cache: PriceCache) -> list[dict]:
    """Rank every user profile by live total portfolio value.

    Returns entries sorted by rank:
    ``[{"user_id", "name", "total_value", "return_pct", "rank"}, ...]``.
    """
    users = conn.execute(
        "SELECT id, cash_balance, created_at, display_name FROM users_profile"
    ).fetchall()

    values_by_user: dict[str, float] = {}
    for row in conn.execute("SELECT user_id, quantity, avg_cost, ticker FROM positions"):
        price = price_cache.get_price(row["ticker"])
        if price is None:
            price = row["avg_cost"]  # Uncached ticker — value at cost.
        values_by_user[row["user_id"]] = (
            values_by_user.get(row["user_id"], 0.0) + row["quantity"] * price
        )

    entries = [
        {
            "user_id": user["id"],
            "name": _display_name(user),
            "total_value": user["cash_balance"] + values_by_user.get(user["id"], 0.0),
            "created_at": user["created_at"],
        }
        for user in users
    ]
    # Rank by total_value desc; ties go to the earlier created_at, then id.
    entries.sort(key=lambda e: (-e["total_value"], e["created_at"], e["user_id"]))

    return [
        {
            "user_id": entry["user_id"],
            "name": entry["name"],
            "total_value": round(entry["total_value"], 2),
            "return_pct": round(
                (entry["total_value"] - STARTING_CASH) / STARTING_CASH * 100.0, 2
            ),
            "rank": position,
        }
        for position, entry in enumerate(entries, start=1)
    ]


def get_current_season(conn: sqlite3.Connection) -> sqlite3.Row:
    """Return the current season row (ended_at IS NULL, newest first).

    ``init_db`` guarantees season 1 exists; if every season was somehow ended
    (hand-edited DB), a fresh one is inserted so the leaderboard always has a
    current season.
    """
    row = conn.execute(
        "SELECT id, started_at, ended_at FROM seasons "
        "WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row

    conn.execute(
        "INSERT INTO seasons (started_at) VALUES (?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    return conn.execute(
        "SELECT id, started_at, ended_at FROM seasons "
        "WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()


def create_leaderboard_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the leaderboard APIRouter with injected dependencies."""
    router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

    @router.get("")
    async def get_leaderboard() -> dict:
        """Current-season standings across every user (public — no auth)."""
        conn = get_conn(db_path)
        try:
            season = get_current_season(conn)
            entries = compute_standings(conn, price_cache)
        finally:
            conn.close()
        return {
            "season": {"id": season["id"], "started_at": season["started_at"]},
            "entries": entries,
        }

    return router
