"""Season API routes for FinAlly (M4.3) — archive standings, restart everyone.

Provides:
- POST /api/season/reset — {"confirm": true} required (missing/false → 400
  {"error": "Confirmation required"}). In ONE transaction: archives the
  current standings into ``season_results``, stamps the current season's
  ``ended_at``, inserts the next season, and resets EVERY user — cash back to
  $10,000, positions deleted, open orders cancelled, active rules paused.
  Trades, chat messages, and snapshots are kept as history.

    200 {"season": {"id", "started_at", "ended_at": null},
         "archived": {"season_id": int,
                      "entries": [{"user_id", "name", "final_value",
                                   "return_pct", "rank"}]}}

- GET /api/seasons — every season, newest first; archived results only for
  ended seasons (the current season's ``results`` is null):

    {"seasons": [{"id", "started_at", "ended_at",
                  "results": [...archived entries...] | null}]}

Routes are created via the factory ``create_seasons_router`` closing over the
shared ``PriceCache`` and database path. The reset is deliberately unauthenticated
admin-lite (it's a classroom sim); the confirm gate is the only guard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.leaderboard import compute_standings, get_current_season

logger = logging.getLogger(__name__)


class SeasonResetRequest(BaseModel):
    confirm: bool = False


def create_seasons_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the seasons APIRouter with injected dependencies."""
    router = APIRouter(tags=["seasons"])

    @router.post("/api/season/reset")
    async def reset_season(body: SeasonResetRequest | None = None) -> dict:
        """Archive the current season and restart everyone at $10,000.

        Requires ``{"confirm": true}``; anything else returns HTTP 400 and
        changes nothing. All writes land in a single transaction.
        """
        if body is None or not body.confirm:
            return JSONResponse(
                status_code=400, content={"error": "Confirmation required"}
            )

        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn(db_path)
        try:
            # get_current_season may lazily insert (hand-edited DB) — do it
            # before taking the write lock so the reset transaction below is
            # a single BEGIN IMMEDIATE block.
            season = get_current_season(conn)

            conn.execute("BEGIN IMMEDIATE")
            standings = compute_standings(conn, price_cache)
            archived_entries = [
                {
                    "user_id": entry["user_id"],
                    "name": entry["name"],
                    "final_value": entry["total_value"],
                    "return_pct": entry["return_pct"],
                    "rank": entry["rank"],
                }
                for entry in standings
            ]
            for entry in archived_entries:
                conn.execute(
                    "INSERT OR REPLACE INTO season_results "
                    "(season_id, user_id, name, final_value, return_pct, rank) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        season["id"],
                        entry["user_id"],
                        entry["name"],
                        entry["final_value"],
                        entry["return_pct"],
                        entry["rank"],
                    ),
                )

            conn.execute(
                "UPDATE seasons SET ended_at = ? WHERE id = ?", (now, season["id"])
            )
            cur = conn.execute("INSERT INTO seasons (started_at) VALUES (?)", (now,))
            new_season_id = cur.lastrowid

            # Reset every user: fresh $10k, flat book. Trades/chat/snapshots
            # stay — they are the historical record of past seasons.
            conn.execute("UPDATE users_profile SET cash_balance = 10000.0")
            conn.execute("DELETE FROM positions")
            conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE status = 'open'"
            )
            conn.execute(
                "UPDATE rules SET status = 'paused' WHERE status = 'active'"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Season reset failed — rolled back")
            raise
        finally:
            conn.close()

        logger.info(
            "Season %s archived (%d entries); season %s started",
            season["id"], len(archived_entries), new_season_id,
        )
        return {
            "season": {"id": new_season_id, "started_at": now, "ended_at": None},
            "archived": {"season_id": season["id"], "entries": archived_entries},
        }

    @router.get("/api/seasons")
    async def list_seasons() -> dict:
        """Every season, newest first; results only for ended seasons."""
        conn = get_conn(db_path)
        try:
            season_rows = conn.execute(
                "SELECT id, started_at, ended_at FROM seasons ORDER BY id DESC"
            ).fetchall()
            result_rows = conn.execute(
                "SELECT season_id, user_id, name, final_value, return_pct, rank "
                "FROM season_results ORDER BY season_id, rank ASC"
            ).fetchall()
        finally:
            conn.close()

        results_by_season: dict[int, list[dict]] = {}
        for row in result_rows:
            results_by_season.setdefault(row["season_id"], []).append(
                {
                    "user_id": row["user_id"],
                    "name": row["name"],
                    "final_value": row["final_value"],
                    "return_pct": row["return_pct"],
                    "rank": row["rank"],
                }
            )

        return {
            "seasons": [
                {
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "results": (
                        results_by_season.get(row["id"], [])
                        if row["ended_at"] is not None
                        else None
                    ),
                }
                for row in season_rows
            ]
        }

    return router
