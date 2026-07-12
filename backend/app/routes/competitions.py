"""Timed private competitions API for FinAlly (D2 §3).

Endpoints (all under /api/competitions):

- POST ``''`` ``{name 1..40, hours 1..168}`` → 201 ``{"competition": {...}}``.
  COOKIE IDENTITY ONLY: Bearer calls get 403 (the keys.py ``_bearer_rejection``
  red-line pattern) so a leaked API key can never spawn competitions. The
  creator auto-joins with a ``baseline_value`` snapshotted at the
  ``compute_standings`` caliber (cash + Σ quantity × live cache price, avg-cost
  fallback, 2dp). At most ``MAX_RUNNING_PER_USER`` (5) running competitions per
  creator — 400 over the limit.
- POST ``/join`` ``{code}`` → 200 ``{"competition": {...}}``. Bearer IS allowed
  here (deliberate, contract §3: bots may enter the arena themselves — the
  competition loop is part of the game; documented for the record). Only
  running competitions can be joined: unknown code → 404, ended → 400. A
  repeat join is idempotent 200 and never touches the stored baseline.
- GET ``''?scope=mine(default)|all`` → ``{"competitions": [summary, ...]}``.
  ``mine`` lists competitions the caller is a MEMBER of (joined ones
  included); ``all`` lists every competition. ``code`` is revealed only on
  mine-scope rows the caller created — everyone else sees null
  (share-to-join stays creator-controlled).
- GET ``/{competition_id}`` → summary + ``"board"``: ranked member standings
  ``[{user_id, name, baseline_value, value, return_pct, rank}]``. Running
  boards value members with the live ``compute_standings`` caliber; ended
  boards use each member's last ``portfolio_snapshot`` at or before
  ``ends_at`` (the 30s snapshot task plus post-trade snapshots already
  exist — no new background loop). A member with no snapshot falls back to
  their baseline (0% return). Rank is ``return_pct`` descending; ties break
  to the earlier ``joined_at`` (then user id, for full determinism).

Status is derived from time on every read (competitions run NO background
loop): now < starts_at → upcoming, now < ends_at → running, otherwise
ended. This phase stamps ``starts_at = created_at``, so competitions are
born running (upcoming is unreachable until scheduled starts ship).

Design notes for the record (contract §3):
- Competitions do NOT isolate money: one portfolio may compete in several
  competitions at once — a deliberate teaching trade-off.
- A season reset (M4.3) mid-competition clears every portfolio; running
  boards reflect the reset honestly (values drop to the fresh seed cash).
- Join codes are 6 chars drawn from A-Z2-9 minus the confusable I/O (0/1
  are excluded by construction), UNIQUE at the database level.

Factory ``create_competitions_router(price_cache, db_path)`` mirrors the
other routers.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import get_current_user_id
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.routes.leaderboard import compute_standings

logger = logging.getLogger(__name__)

# Join-code alphabet: A-Z2-9 minus the confusable I/O (0 and 1 are already
# excluded by starting the digit range at 2) — contract §3.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
_CODE_ATTEMPTS = 32  # 32^6 ≈ 1e9 codes — collisions are practically impossible.

NAME_MIN_LEN = 1
NAME_MAX_LEN = 40
MIN_HOURS = 1
MAX_HOURS = 168  # one week
MAX_RUNNING_PER_USER = 5


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _bearer_rejection(request: Request) -> JSONResponse | None:
    """403 for any Bearer-authenticated call (creation is cookie-only).

    Replicates routes/keys.py ``_bearer_rejection`` belt-and-braces: both the
    gateway-authenticated marker (``request.state.api_key_id``) and a raw
    ``Authorization: Bearer`` header are rejected, so a leaked API key can
    never create competitions even in an app that mounts this router without
    the gateway middleware. Join/read endpoints deliberately do NOT call
    this — bots may enter and watch competitions (contract §3).
    """
    if getattr(request.state, "api_key_id", None) is not None:
        return _error(403, "API keys cannot create competitions")
    auth_header = request.headers.get("authorization", "")
    if auth_header[:7].lower() == "bearer ":
        return _error(403, "API keys cannot create competitions")
    return None


async def _read_json_object(request: Request) -> dict | None:
    """Parse the request body as a JSON object; None if it isn't one."""
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _validate_name(value: Any) -> tuple[str | None, str | None]:
    """Return (normalized_name, error). 1..40 chars after strip."""
    if not isinstance(value, str):
        return None, "Name must be 1-40 characters"
    name = value.strip()
    if not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
        return None, "Name must be 1-40 characters"
    return name, None


def _validate_hours(value: Any) -> tuple[int | None, str | None]:
    """Return (hours, error). Whole hours 1..168 (bools rejected)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "hours must be an integer between 1 and 168"
    if not (MIN_HOURS <= value <= MAX_HOURS):
        return None, "hours must be an integer between 1 and 168"
    return value, None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _competition_status(starts_at: str, ends_at: str, now: datetime) -> str:
    """Derive the lifecycle status from time — no state is stored.

    Unparsable timestamps (hand-edited DB) degrade to 'ended' — a frozen
    competition, never a crash.
    """
    try:
        starts = datetime.fromisoformat(starts_at)
        ends = datetime.fromisoformat(ends_at)
        if now < starts:
            return "upcoming"
        if now < ends:
            return "running"
    except (ValueError, TypeError):
        # ValueError: unparsable text; TypeError: naive timestamp compared
        # against the aware `now` (both only via hand-edited rows).
        pass
    return "ended"


def _generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _unique_code(conn: sqlite3.Connection) -> str | None:
    """A join code no existing competition uses (checked in-transaction)."""
    for _ in range(_CODE_ATTEMPTS):
        code = _generate_code()
        row = conn.execute(
            "SELECT 1 FROM competitions WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return code
    return None


def _display_name(user_id: str, display_name: str | None) -> str:
    """Public name for a member (mirrors leaderboard's display rule)."""
    if display_name:
        return display_name
    return "Guest" if user_id == "default" else user_id


def create_competitions_router(price_cache: PriceCache, db_path: str) -> APIRouter:
    """Factory: build the /api/competitions APIRouter with injected deps.

    Args:
        price_cache: Live price cache — baselines and running boards value
            positions at the ``compute_standings`` caliber.
        db_path: Path to the SQLite database file.
    """
    router = APIRouter(prefix="/api/competitions", tags=["competitions"])

    def _standings_values(conn: sqlite3.Connection) -> dict[str, float]:
        """user_id → live total_value (2dp), the compute_standings caliber."""
        return {
            entry["user_id"]: entry["total_value"]
            for entry in compute_standings(conn, price_cache)
        }

    def _member_count(conn: sqlite3.Connection, competition_id: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM competition_members WHERE competition_id = ?",
            (competition_id,),
        ).fetchone()[0]

    def _summary(
        row: sqlite3.Row, member_count: int, now: datetime, reveal_code: bool
    ) -> dict:
        """List/detail summary shape. ``code`` is null unless revealed."""
        return {
            "id": row["id"],
            "name": row["name"],
            "code": row["code"] if reveal_code else None,
            "status": _competition_status(row["starts_at"], row["ends_at"], now),
            "member_count": member_count,
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
        }

    @router.post("")
    async def create_competition(request: Request) -> JSONResponse:
        """Create a competition (cookie only) and auto-join its creator."""
        rejection = _bearer_rejection(request)
        if rejection is not None:
            return rejection
        user_id = get_current_user_id(request, db_path)

        payload = await _read_json_object(request)
        if payload is None:
            return _error(400, "Invalid JSON body")
        name, err = _validate_name(payload.get("name"))
        if err is not None:
            return _error(400, err)
        hours, err = _validate_hours(payload.get("hours"))
        if err is not None:
            return _error(400, err)

        now = _utc_now()
        now_iso = now.isoformat()
        ends_iso = (now + timedelta(hours=hours)).isoformat()
        competition_id = str(uuid.uuid4())

        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Running = ends_at still in the future (starts_at = created_at,
            # so every non-ended competition is running). Same +00:00 ISO
            # format everywhere → lexicographic compare is chronological.
            running = conn.execute(
                "SELECT COUNT(*) FROM competitions "
                "WHERE created_by = ? AND ends_at > ?",
                (user_id, now_iso),
            ).fetchone()[0]
            if running >= MAX_RUNNING_PER_USER:
                conn.rollback()
                return _error(
                    400,
                    f"Competition limit reached ({MAX_RUNNING_PER_USER} "
                    "running per user)",
                )
            code = _unique_code(conn)
            if code is None:  # pragma: no cover — 32 collisions in a row
                conn.rollback()
                return _error(500, "Could not allocate a unique join code")
            baseline = _standings_values(conn).get(user_id, 0.0)
            conn.execute(
                "INSERT INTO competitions "
                "(id, name, code, created_by, starts_at, ends_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (competition_id, name, code, user_id, now_iso, ends_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO competition_members "
                "(competition_id, user_id, joined_at, baseline_value) "
                "VALUES (?, ?, ?, ?)",
                (competition_id, user_id, now_iso, baseline),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            "Competition %s (%r, %dh) created by user %r",
            competition_id, name, hours, user_id,
        )
        return JSONResponse(
            status_code=201,
            content={"competition": _summary(row, 1, now, reveal_code=True)},
        )

    @router.post("/join")
    async def join_competition(request: Request):
        """Join a running competition by invite code (Bearer allowed).

        Idempotent: a member joining again gets 200 with the current state
        and their stored baseline stays untouched.
        """
        # NO _bearer_rejection here — deliberate (contract §3): bots may
        # sign themselves up for the arena via their API key.
        user_id = get_current_user_id(request, db_path)

        payload = await _read_json_object(request)
        if payload is None:
            return _error(400, "Invalid JSON body")
        raw_code = payload.get("code")
        if not isinstance(raw_code, str) or not raw_code.strip():
            return _error(400, "code is required")
        code = raw_code.strip().upper()

        now = _utc_now()
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM competitions WHERE code = ?", (code,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return _error(404, "Competition not found")
            status = _competition_status(row["starts_at"], row["ends_at"], now)
            if status != "running":
                conn.rollback()
                message = (
                    "Competition has ended"
                    if status == "ended"
                    else "Competition has not started"
                )
                return _error(400, message)
            existing = conn.execute(
                "SELECT 1 FROM competition_members "
                "WHERE competition_id = ? AND user_id = ?",
                (row["id"], user_id),
            ).fetchone()
            if existing is None:
                baseline = _standings_values(conn).get(user_id, 0.0)
                conn.execute(
                    "INSERT INTO competition_members "
                    "(competition_id, user_id, joined_at, baseline_value) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], user_id, now.isoformat(), baseline),
                )
                conn.commit()
                logger.info(
                    "User %r joined competition %s (%r)",
                    user_id, row["id"], row["name"],
                )
            else:
                # Repeat join: nothing to write — release the write lock.
                conn.rollback()
            member_count = _member_count(conn, row["id"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {
            "competition": _summary(
                row, member_count, now, reveal_code=row["created_by"] == user_id
            )
        }

    @router.get("")
    async def list_competitions(request: Request, scope: str | None = None):
        """List competitions: mine (member of, default) or all."""
        scope_value = (scope or "mine").strip().lower()
        if scope_value not in ("mine", "all"):
            return _error(400, "scope must be 'mine' or 'all'")
        user_id = get_current_user_id(request, db_path)

        now = _utc_now()
        conn = get_conn(db_path)
        try:
            if scope_value == "mine":
                rows = conn.execute(
                    "SELECT c.* FROM competitions AS c "
                    "JOIN competition_members AS m ON m.competition_id = c.id "
                    "WHERE m.user_id = ? "
                    "ORDER BY c.created_at DESC, c.rowid DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM competitions "
                    "ORDER BY created_at DESC, rowid DESC"
                ).fetchall()
            counts = {
                count_row["competition_id"]: count_row["n"]
                for count_row in conn.execute(
                    "SELECT competition_id, COUNT(*) AS n "
                    "FROM competition_members GROUP BY competition_id"
                )
            }
        finally:
            conn.close()

        return {
            "competitions": [
                _summary(
                    row,
                    counts.get(row["id"], 0),
                    now,
                    # code 仅 mine 且本人创建 (contract §3) — null otherwise.
                    reveal_code=(
                        scope_value == "mine" and row["created_by"] == user_id
                    ),
                )
                for row in rows
            ]
        }

    @router.get("/{competition_id}")
    async def get_competition(competition_id: str, request: Request):
        """Competition detail + ranked board (visible to every identity)."""
        user_id = get_current_user_id(request, db_path)

        now = _utc_now()
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM competitions WHERE id = ?", (competition_id,)
            ).fetchone()
            if row is None:
                return _error(404, "Competition not found")
            members = conn.execute(
                "SELECT m.user_id, m.joined_at, m.baseline_value, u.display_name "
                "FROM competition_members AS m "
                "LEFT JOIN users_profile AS u ON u.id = m.user_id "
                "WHERE m.competition_id = ?",
                (row["id"],),
            ).fetchall()

            status = _competition_status(row["starts_at"], row["ends_at"], now)
            values: dict[str, float] = {}
            if status == "ended":
                # Final value = the member's last snapshot at or before
                # ends_at (same +00:00 ISO format → lexicographic compare).
                # Missing snapshot → baseline fallback (0% return).
                for member in members:
                    snap = conn.execute(
                        "SELECT total_value FROM portfolio_snapshots "
                        "WHERE user_id = ? AND recorded_at <= ? "
                        "ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
                        (member["user_id"], row["ends_at"]),
                    ).fetchone()
                    if snap is not None:
                        values[member["user_id"]] = snap["total_value"]
            else:
                # Running (and the unreachable upcoming) boards are live.
                values = _standings_values(conn)
        finally:
            conn.close()

        entries = []
        for member in members:
            baseline: float = member["baseline_value"]
            value = values.get(member["user_id"])
            if value is None:
                value = baseline
            return_pct = (
                round((value - baseline) / baseline * 100.0, 2)
                if baseline > 0
                else 0.0
            )
            entries.append(
                {
                    "user_id": member["user_id"],
                    "name": _display_name(member["user_id"], member["display_name"]),
                    "baseline_value": round(baseline, 2),
                    "value": round(value, 2),
                    "return_pct": return_pct,
                    "joined_at": member["joined_at"],
                }
            )
        # Rank: return_pct desc; ties to the earlier joined_at, then user id.
        entries.sort(key=lambda e: (-e["return_pct"], e["joined_at"], e["user_id"]))
        board = [
            {
                "user_id": entry["user_id"],
                "name": entry["name"],
                "baseline_value": entry["baseline_value"],
                "value": entry["value"],
                "return_pct": entry["return_pct"],
                "rank": position,
            }
            for position, entry in enumerate(entries, start=1)
        ]

        summary = _summary(
            row, len(members), now, reveal_code=row["created_by"] == user_id
        )
        return {**summary, "board": board}

    return router
