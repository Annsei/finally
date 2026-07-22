"""Season API routes for FinAlly (M4.3) — archive standings, restart everyone.

Provides:
- POST /api/season/reset — {"confirm": true} required (missing/false → 400
  {"error": "Confirmation required"}). In ONE transaction: archives the
  current standings into ``season_results``, stamps the current season's
  ``ended_at``, inserts the next season, and resets EVERY user — cash back to
  profile seed cash, positions deleted, open orders cancelled, active rules
  paused, and live strategy state cleared. Trades, chat messages, and
  snapshots are kept as history.

    200 {"season": {"id", "started_at", "ended_at": null},
         "archived": {"season_id": int,
                      "entries": [{"user_id", "name", "final_value",
                                   "return_pct", "rank"}]}}

- GET /api/seasons — every season, newest first; archived results only for
  ended seasons (the current season's ``results`` is null):

    {"seasons": [{"id", "started_at", "ended_at",
                  "results": [...archived entries...] | null}]}

Routes are created via the factory ``create_seasons_router`` closing over the
shared ``PriceCache`` and database path. Reset always requires an
``Idempotency-Key``; the ``X-FinAlly-Admin-Token`` header is mandatory in
classroom-server mode and optional-but-validated in local-demo, so the local
reset button keeps working without configuration. Both checks run before the
transaction.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.leaderboard import compute_standings
from app.settings import RuntimeSettings

logger = logging.getLogger(__name__)

ADMIN_HEADER = "x-finally-admin-token"
IDEMPOTENCY_HEADER = "idempotency-key"
RESET_ACTION = "season.reset"


class SeasonResetRequest(BaseModel):
    confirm: bool = False


def create_seasons_router(
    price_cache: PriceCache,
    db_path: str,
    seed_cash: float = 10_000.0,
    settings: RuntimeSettings | None = None,
) -> APIRouter:
    """Factory: build the seasons APIRouter with injected dependencies.

    ``seed_cash`` is the amount every user's cash resets to on a season
    reset and the baseline for the archived return percentages (CN-1: the
    active market profile's seed cash; default keeps the US $10,000).
    """
    router = APIRouter(tags=["seasons"])
    runtime = settings or RuntimeSettings()

    @router.post("/api/season/reset")
    async def reset_season(
        request: Request, body: SeasonResetRequest | None = None
    ) -> dict:
        """Archive the current season and restart everyone at profile seed cash.

        Requires ``{"confirm": true}``; anything else returns HTTP 400 and
        changes nothing. All writes land in a single transaction.
        """
        # Admin token: REQUIRED in classroom-server mode (shared deployment —
        # a reset touches every user). Local-demo does not require it (the
        # loopback demo's reset button works out of the box), but a supplied
        # token is still validated in every mode.
        supplied_token = request.headers.get(ADMIN_HEADER)
        if runtime.is_server and not supplied_token:
            return JSONResponse(
                status_code=401, content={"error": "Administrator token required"}
            )
        if supplied_token:
            expected_token = runtime.admin_token or ""
            if not expected_token or not secrets.compare_digest(
                supplied_token, expected_token
            ):
                return JSONResponse(
                    status_code=403, content={"error": "Invalid administrator token"}
                )
        if body is None or not body.confirm:
            return JSONResponse(
                status_code=400, content={"error": "Confirmation required"}
            )
        request_id = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        if not request_id or len(request_id) > 128:
            return JSONResponse(
                status_code=400,
                content={"error": "Idempotency-Key header is required (max 128 chars)"},
            )

        now = datetime.now(timezone.utc).isoformat()
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            replay = conn.execute(
                "SELECT details FROM admin_audit "
                "WHERE action = ? AND request_id = ? AND result = 'ok'",
                (RESET_ACTION, request_id),
            ).fetchone()
            if replay is not None and replay["details"]:
                conn.rollback()
                return json.loads(replay["details"])

            # Read the current season only after taking the write lock. This
            # prevents two concurrent resets from archiving the same season and
            # creating two rows with ended_at IS NULL.
            season = conn.execute(
                "SELECT id, started_at, ended_at FROM seasons "
                "WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if season is None:
                cur = conn.execute("INSERT INTO seasons (started_at) VALUES (?)", (now,))
                season = conn.execute(
                    "SELECT id, started_at, ended_at FROM seasons WHERE id = ?",
                    (cur.lastrowid,),
                ).fetchone()
            standings = compute_standings(conn, price_cache, seed_cash)
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

            # Reset every user: fresh seed cash, flat book. Trades/chat/
            # snapshots stay — they are the historical record of past seasons.
            conn.execute("UPDATE users_profile SET cash_balance = ?", (seed_cash,))
            conn.execute("DELETE FROM positions")
            conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE status = 'open'"
            )
            conn.execute(
                "UPDATE rules SET status = 'paused' WHERE status = 'active'"
            )
            conn.execute(
                "UPDATE strategies SET status = 'paused', open_qty = 0, "
                "open_price = NULL, opened_at = NULL, high_water = NULL, "
                "cooldown_until = NULL WHERE status = 'live' OR open_qty > 0"
            )
            result = {
                "season": {"id": new_season_id, "started_at": now, "ended_at": None},
                "archived": {"season_id": season["id"], "entries": archived_entries},
            }
            conn.execute(
                "INSERT INTO admin_audit "
                "(id, action, request_id, result, details, created_at) "
                "VALUES (?, ?, ?, 'ok', ?, ?)",
                (
                    str(uuid.uuid4()),
                    RESET_ACTION,
                    request_id,
                    json.dumps(result, separators=(",", ":")),
                    now,
                ),
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
        return result

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
