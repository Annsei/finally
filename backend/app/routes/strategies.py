"""Strategy CRUD, state machine, performance, and templates (P2 §6).

Provides:
- GET    /api/strategies                   — list (status filter; the default
  view hides archived rows, ``status=all`` includes them)
- POST   /api/strategies                   — create a draft strategy
- GET    /api/strategies/templates         — the six-template static registry
  (no auth — the registry is public, names/descriptions render via i18n keys)
- GET    /api/strategies/{id}              — single strategy
- PATCH  /api/strategies/{id}              — status transition OR config edit
- DELETE /api/strategies/{id}              — delete (live strategies refuse)
- GET    /api/strategies/{id}/performance  — realized-P&L stats + 0-baseline
  equity curve + this strategy's fills

State machine (contract §6): draft→live (stamps deployed_at; requires at
least one exit via ``indicators.has_any_exit``), live↔paused, any→archived
(clears the engine's open state — the shares stay in the portfolio for the
user to handle), archived is terminal (any further transition → 400).
Config edits (name/entry/exits/sizing) are rejected with 400 while live
("pause first"). DELETE on a live strategy → 400; deletion keeps
``trades.strategy_id`` attribution (append-only log, no FK).

Shared helpers for the chat pipeline (§7):
- ``create_strategy_on_conn`` — validate + insert one draft strategy on an
  open connection WITHOUT committing; returns ``{"status": "created",
  "strategy": {...}}`` or ``{"status": "failed", "ticker": T, "error": msg}``
  and never raises on validation failure.
- ``transition_strategy_on_conn`` — apply one state-machine transition on an
  open connection WITHOUT committing; returns None on success or the error
  message (callers map it to HTTP 400 / a failed chat outcome).
- ``resolve_strategy_on_conn`` — find a user's strategy by id or
  case-insensitive name (newest name-match wins).

Performance math reuses the M5/analytics conventions: ``win_rate`` is
``round(wins / round_trips, 4)`` (the analytics endpoint's 4dp rounding),
``max_drawdown_pct`` runs the same ``_max_drawdown_pct`` helper the
portfolio analytics use, and ``profit_factor`` is the backtest's
``round(gross_wins / gross_losses, 2)`` (None when loss-free). The equity
curve is the cumulative realized P&L at each sell (0-baseline — the frontend
renders it with BaselineSeries base 0) plus, while a position is open, a
final mark-to-market point that adds the unrealized P&L at the live quote.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.connection import get_conn
from app.indicators import (
    has_any_exit,
    validate_condition_group,
    validate_exits,
    validate_sizing,
)
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile
from app.market.seed_prices import SEED_PRICES
from app.mechanics import lot_size_error
from app.routes.portfolio import _max_drawdown_pct

logger = logging.getLogger(__name__)

STRATEGY_STATUSES = ("draft", "live", "paused", "archived")
NAME_MAX_LEN = 40

_STRATEGY_SELECT_COLUMNS = (
    "id, user_id, name, ticker, status, entry, exits, sizing, template, "
    "created_at, deployed_at, open_qty, open_price, opened_at, high_water, "
    "cooldown_until, entered_count, exited_count, last_fired_at"
)

# ---------------------------------------------------------------------------
# Template registry (contract §6 — six templates, fixed). Names/descriptions
# are frontend i18n (`strategy.template.{key}.name/.desc`); the backend only
# serves the machine config. ticker_hint is null on every template — the user
# (or the AI) picks the symbol.
# ---------------------------------------------------------------------------
STRATEGY_TEMPLATES: list[dict] = [
    {
        "key": "dip_buyer",
        "ticker_hint": None,
        "entry": {"all": [{"field": "day_change_pct", "op": "below", "value": -3}]},
        "exits": {"take_profit_pct": 4, "stop_loss_pct": 3},
        "sizing": {"mode": "cash_pct", "pct": 20},
    },
    {
        "key": "momentum_breakout",
        "ticker_hint": None,
        "entry": {
            "all": [{"field": "window_high", "op": "above", "params": {"minutes": 60}}]
        },
        "exits": {"trailing_stop_pct": 2.5, "stop_loss_pct": 3},
        "sizing": {"mode": "cash_pct", "pct": 20},
    },
    {
        "key": "ma_golden_cross",
        "ticker_hint": None,
        "entry": {
            "all": [
                {"field": "ma_cross", "op": "above", "params": {"fast": 5, "slow": 20}}
            ]
        },
        "exits": {"take_profit_pct": 5, "stop_loss_pct": 3},
        "sizing": {"mode": "cash_pct", "pct": 25},
    },
    {
        "key": "grid_lite",
        "ticker_hint": None,
        "entry": {
            "all": [
                {
                    "field": "pullback_from_high_pct",
                    "op": "above",
                    "value": 2,
                    "params": {"minutes": 60},
                }
            ]
        },
        "exits": {"take_profit_pct": 2, "stop_loss_pct": 6},
        "sizing": {"mode": "cash_pct", "pct": 15},
    },
    {
        "key": "rsi_rebound",
        "ticker_hint": None,
        "entry": {
            "all": [
                {"field": "rsi", "op": "below", "value": 30, "params": {"period": 14}}
            ]
        },
        "exits": {"take_profit_pct": 4, "stop_loss_pct": 3},
        "sizing": {"mode": "cash_pct", "pct": 20},
    },
    {
        "key": "trend_rider",
        "ticker_hint": None,
        "entry": {
            "all": [
                {"field": "ma", "op": "above", "value": 0, "params": {"period": 30}},
                {"field": "day_change_pct", "op": "above", "value": 0.5},
            ]
        },
        "exits": {"trailing_stop_pct": 3},
        "sizing": {"mode": "cash_pct", "pct": 25},
    },
]

TEMPLATES_BY_KEY: dict[str, dict] = {t["key"]: t for t in STRATEGY_TEMPLATES}


class CreateStrategyRequest(BaseModel):
    name: str
    ticker: str
    entry: dict
    exits: dict | None = None
    sizing: dict
    template: str | None = None


class UpdateStrategyRequest(BaseModel):
    """PATCH body: EITHER a status transition OR a config edit (contract §6)."""

    status: str | None = None
    name: str | None = None
    entry: dict | None = None
    exits: dict | None = None
    sizing: dict | None = None


def _parse_json(value):
    """Parse a stored JSON TEXT column; malformed data degrades to {}."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, (dict, list)) else {}
    return value if value is not None else {}


def strategy_row_to_dict(
    row: sqlite3.Row, *, runs_count: int = 0, realized_pnl: float = 0.0
) -> dict:
    """Serialize a ``strategies`` row to the public JSON shape (contract §6)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "ticker": row["ticker"],
        "status": row["status"],
        "entry": _parse_json(row["entry"]),
        "exits": _parse_json(row["exits"]),
        "sizing": _parse_json(row["sizing"]),
        "template": row["template"],
        "created_at": row["created_at"],
        "deployed_at": row["deployed_at"],
        "open_qty": row["open_qty"],
        "open_price": row["open_price"],
        "opened_at": row["opened_at"],
        "entered_count": row["entered_count"],
        "exited_count": row["exited_count"],
        "last_fired_at": row["last_fired_at"],
        "runs_count": runs_count,
        "realized_pnl": round(realized_pnl, 2),
    }


def _validate_strategy_config(
    price_cache: PriceCache,
    *,
    name: str,
    ticker: str,
    entry,
    exits,
    sizing,
    universe=None,
    profile: MarketProfile | None = None,
) -> str | None:
    """Full create/edit validation (contract §6). Returns None or a message.

    - name 1..40 chars after trimming
    - ticker must be known: live cache quote or the market's seed prices
    - entry/exits/sizing via the §2 whitelist validators
    - fixed_qty sizing must be whole board lots on lot-sized profiles (CN),
      mirroring ``normalize_strategy_backtest_config``
    """
    if not 1 <= len(name) <= NAME_MAX_LEN:
        return f"name must be 1 to {NAME_MAX_LEN} characters"
    seeds = SEED_PRICES if universe is None else universe.seed_prices
    if price_cache.get_price(ticker) is None and ticker not in seeds:
        return "Ticker not found"
    error = validate_condition_group(entry)
    if error is not None:
        return f"entry: {error}"
    error = validate_exits(exits)
    if error is not None:
        return f"exits: {error}"
    error = validate_sizing(sizing)
    if error is not None:
        return f"sizing: {error}"
    if sizing["mode"] == "fixed_qty":
        lot_error = lot_size_error(profile, "buy", float(sizing["qty"]))
        if lot_error is not None:
            return lot_error
    return None


def create_strategy_on_conn(
    conn: sqlite3.Connection,
    price_cache: PriceCache,
    *,
    name: str,
    ticker: str,
    entry,
    exits,
    sizing,
    template: str | None = None,
    user_id: str = "default",
    universe=None,
    profile: MarketProfile | None = None,
) -> dict:
    """Validate and insert one DRAFT strategy on an open connection.

    Shared creation path for POST /api/strategies and the chat 'create'
    action (§7). Does NOT commit — the caller owns the transaction boundary
    (the chat flow batches the insert with the rest of the turn). All
    validation failures return ``{"status": "failed", "ticker": T,
    "error": msg}`` and never raise.
    """
    name = (name or "").strip()
    ticker = (ticker or "").strip().upper()

    error = _validate_strategy_config(
        price_cache,
        name=name,
        ticker=ticker,
        entry=entry,
        exits=exits,
        sizing=sizing,
        universe=universe,
        profile=profile,
    )
    if error is not None:
        return {"status": "failed", "ticker": ticker, "error": error}

    # Normalized storage: drop unset exit keys; coerce sizing numbers.
    exits = {} if exits is None else {k: v for k, v in exits.items() if v is not None}
    if sizing["mode"] == "fixed_qty":
        sizing = {"mode": "fixed_qty", "qty": float(sizing["qty"])}
    else:
        sizing = {"mode": "cash_pct", "pct": float(sizing["pct"])}

    strategy_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO strategies (id, user_id, name, ticker, status, entry, exits,
            sizing, template, created_at)
        VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            user_id,
            name,
            ticker,
            json.dumps(entry),
            json.dumps(exits),
            json.dumps(sizing),
            template,
            created_at,
        ),
    )
    return {
        "status": "created",
        "strategy": {
            "id": strategy_id,
            "name": name,
            "ticker": ticker,
            "status": "draft",
            "entry": entry,
            "exits": exits,
            "sizing": sizing,
            "template": template,
            "created_at": created_at,
            "deployed_at": None,
            "open_qty": 0.0,
            "open_price": None,
            "opened_at": None,
            "entered_count": 0,
            "exited_count": 0,
            "last_fired_at": None,
            "runs_count": 0,
            "realized_pnl": 0.0,
        },
    }


def transition_strategy_on_conn(
    conn: sqlite3.Connection, row: sqlite3.Row, new_status: str
) -> str | None:
    """Apply one state-machine transition (contract §6). None on success.

    Legal moves: draft→live (requires at least one exit; stamps deployed_at),
    paused→live (resume — same exit gate: a paused strategy's exits may have
    been edited away), live→paused, and any non-archived→archived (clears the
    engine's open state; the shares stay in the portfolio). ``archived`` is
    terminal. Does NOT commit — the caller owns the transaction.
    """
    current = row["status"]
    if new_status not in STRATEGY_STATUSES:
        return "status must be one of 'draft', 'live', 'paused', 'archived'"
    if current == "archived":
        return "Strategy is archived — archived is a terminal state"
    if new_status == "live":
        if current not in ("draft", "paused"):
            return f"Cannot deploy a strategy from status '{current}'"
        if not has_any_exit(_parse_json(row["exits"])):
            return (
                "Deploy requires at least one exit (take_profit_pct, "
                "stop_loss_pct, trailing_stop_pct, or max_holding_days)"
            )
        conn.execute(
            "UPDATE strategies SET status = 'live', "
            "deployed_at = COALESCE(deployed_at, ?) WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        return None
    if new_status == "paused":
        if current != "live":
            return "Only a live strategy can be paused"
        conn.execute(
            "UPDATE strategies SET status = 'paused' WHERE id = ?", (row["id"],)
        )
        return None
    if new_status == "archived":
        # Archive clears the engine's open/cooldown state — the shares stay
        # in the portfolio for the user to handle manually (contract §3).
        conn.execute(
            """
            UPDATE strategies
            SET status = 'archived', open_qty = 0, open_price = NULL,
                opened_at = NULL, high_water = NULL, cooldown_until = NULL
            WHERE id = ?
            """,
            (row["id"],),
        )
        return None
    return f"Cannot move a strategy from '{current}' to '{new_status}'"


def resolve_strategy_on_conn(
    conn: sqlite3.Connection, user_id: str, reference: str
) -> sqlite3.Row | None:
    """Find a user's strategy by id, else by case-insensitive name (§7).

    The name lookup returns the NEWEST match so a just-created strategy wins
    over older rows with the same name (names are not unique).
    """
    row = conn.execute(
        f"SELECT {_STRATEGY_SELECT_COLUMNS} FROM strategies "
        "WHERE id = ? AND user_id = ?",
        (reference, user_id),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        f"SELECT {_STRATEGY_SELECT_COLUMNS} FROM strategies "
        "WHERE user_id = ? AND lower(name) = lower(?) "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (user_id, (reference or "").strip()),
    ).fetchone()


def _runs_count_map(conn: sqlite3.Connection, user_id: str) -> dict[str, int]:
    return {
        r["strategy_id"]: r["n"]
        for r in conn.execute(
            "SELECT strategy_id, COUNT(*) AS n FROM backtest_runs "
            "WHERE user_id = ? AND strategy_id IS NOT NULL GROUP BY strategy_id",
            (user_id,),
        )
    }


def _realized_pnl_map(conn: sqlite3.Connection, user_id: str) -> dict[str, float]:
    return {
        r["strategy_id"]: r["pnl"] or 0.0
        for r in conn.execute(
            "SELECT strategy_id, COALESCE(SUM(realized_pnl), 0.0) AS pnl "
            "FROM trades WHERE user_id = ? AND strategy_id IS NOT NULL "
            "GROUP BY strategy_id",
            (user_id,),
        )
    }


def _iso_to_epoch(iso_timestamp: str) -> int:
    """ISO timestamp → whole epoch seconds; unparsable data degrades to 0."""
    try:
        return int(datetime.fromisoformat(iso_timestamp).timestamp())
    except (TypeError, ValueError):
        return 0


def create_strategies_router(
    price_cache: PriceCache,
    db_path: str,
    profile: MarketProfile | None = None,
) -> APIRouter:
    """Factory: build the strategies APIRouter with injected dependencies.

    Args:
        price_cache: Shared live price cache — ticker existence check and the
            performance endpoint's open-position mark.
        db_path: Path to the SQLite database file.
        profile: Active market profile (CN) — supplies the universe for the
            ticker check and the 整手 fixed_qty sizing rule. None/us keeps the
            US constants.
    """
    universe = profile.universe if profile is not None else None
    router = APIRouter(prefix="/api/strategies", tags=["strategies"])

    def _get_owned(conn: sqlite3.Connection, strategy_id: str, user_id: str):
        return conn.execute(
            f"SELECT {_STRATEGY_SELECT_COLUMNS} FROM strategies "
            "WHERE id = ? AND user_id = ?",
            (strategy_id, user_id),
        ).fetchone()

    # NOTE: /templates is registered BEFORE /{strategy_id} so the literal
    # path wins route matching.
    @router.get("/templates")
    async def list_templates() -> dict:
        """The six-template static registry (contract §6 — no auth)."""
        return {"templates": STRATEGY_TEMPLATES}

    @router.get("")
    async def list_strategies(request: Request, status: str | None = None) -> dict:
        """List strategies, newest first.

        Query params:
            status: 'draft' | 'live' | 'paused' | 'archived' | 'all'. The
                DEFAULT (absent) view hides archived rows; 'all' includes
                them (contract §6 — pinned). Invalid values → 400.
        """
        status_value = (status or "").strip().lower() or None
        if status_value is not None and status_value != "all" and status_value not in STRATEGY_STATUSES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "status must be one of 'draft', 'live', 'paused', "
                    "'archived', 'all'"
                },
            )

        user_id = get_current_user_id(request, db_path)
        query = f"SELECT {_STRATEGY_SELECT_COLUMNS} FROM strategies WHERE user_id = ?"
        params: list = [user_id]
        if status_value is None:
            query += " AND status != 'archived'"
        elif status_value != "all":
            query += " AND status = ?"
            params.append(status_value)
        query += " ORDER BY created_at DESC, rowid DESC"

        conn = get_conn(db_path)
        try:
            rows = conn.execute(query, params).fetchall()
            runs_counts = _runs_count_map(conn, user_id)
            pnl_map = _realized_pnl_map(conn, user_id)
            return {
                "strategies": [
                    strategy_row_to_dict(
                        row,
                        runs_count=runs_counts.get(row["id"], 0),
                        realized_pnl=pnl_map.get(row["id"], 0.0),
                    )
                    for row in rows
                ]
            }
        finally:
            conn.close()

    @router.post("", status_code=201)
    async def create_strategy(body: CreateStrategyRequest, request: Request):
        """Create a DRAFT strategy (contract §6). Validation failures → 400."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            result = create_strategy_on_conn(
                conn,
                price_cache,
                name=body.name,
                ticker=body.ticker,
                entry=body.entry,
                exits=body.exits,
                sizing=body.sizing,
                template=body.template,
                user_id=user_id,
                universe=universe,
                profile=profile,
            )
            if result["status"] == "failed":
                conn.rollback()
                return JSONResponse(status_code=400, content={"error": result["error"]})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"strategy": result["strategy"]}

    @router.get("/{strategy_id}")
    async def get_strategy(strategy_id: str, request: Request):
        """Single strategy (same shape as the list rows). Unknown/foreign → 404."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            row = _get_owned(conn, strategy_id, user_id)
            if row is None:
                return JSONResponse(
                    status_code=404, content={"error": "Strategy not found"}
                )
            runs_count = conn.execute(
                "SELECT COUNT(*) AS n FROM backtest_runs "
                "WHERE user_id = ? AND strategy_id = ?",
                (user_id, strategy_id),
            ).fetchone()["n"]
            pnl = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0.0) AS pnl FROM trades "
                "WHERE user_id = ? AND strategy_id = ?",
                (user_id, strategy_id),
            ).fetchone()["pnl"]
            return {
                "strategy": strategy_row_to_dict(
                    row, runs_count=runs_count, realized_pnl=pnl or 0.0
                )
            }
        finally:
            conn.close()

    @router.patch("/{strategy_id}")
    async def update_strategy(
        strategy_id: str, body: UpdateStrategyRequest, request: Request
    ):
        """Status transition ({status}) or config edit ({name?/entry?/exits?/sizing?}).

        Status transitions run the §6 state machine (illegal moves → 400).
        Config edits are rejected with 400 while the strategy is live
        ("pause first"). Unknown/foreign ids → 404.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = _get_owned(conn, strategy_id, user_id)
            if row is None:
                conn.rollback()
                return JSONResponse(
                    status_code=404, content={"error": "Strategy not found"}
                )

            if body.status is not None:
                error = transition_strategy_on_conn(
                    conn, row, body.status.strip().lower()
                )
                if error is not None:
                    conn.rollback()
                    return JSONResponse(status_code=400, content={"error": error})
                conn.commit()
            else:
                if (
                    body.name is None
                    and body.entry is None
                    and body.exits is None
                    and body.sizing is None
                ):
                    conn.rollback()
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "Provide a status or at least one of "
                            "name/entry/exits/sizing"
                        },
                    )
                if row["status"] == "live":
                    conn.rollback()
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "Cannot edit a live strategy — pause first"
                        },
                    )
                name = (body.name if body.name is not None else row["name"]).strip()
                entry = body.entry if body.entry is not None else _parse_json(row["entry"])
                exits = body.exits if body.exits is not None else _parse_json(row["exits"])
                sizing = (
                    body.sizing if body.sizing is not None else _parse_json(row["sizing"])
                )
                error = _validate_strategy_config(
                    price_cache,
                    name=name,
                    ticker=row["ticker"],
                    entry=entry,
                    exits=exits,
                    sizing=sizing,
                    universe=universe,
                    profile=profile,
                )
                if error is not None:
                    conn.rollback()
                    return JSONResponse(status_code=400, content={"error": error})
                exits = {k: v for k, v in (exits or {}).items() if v is not None}
                conn.execute(
                    "UPDATE strategies SET name = ?, entry = ?, exits = ?, "
                    "sizing = ? WHERE id = ?",
                    (
                        name,
                        json.dumps(entry),
                        json.dumps(exits),
                        json.dumps(sizing),
                        strategy_id,
                    ),
                )
                conn.commit()

            fresh = _get_owned(conn, strategy_id, user_id)
            runs_count = conn.execute(
                "SELECT COUNT(*) AS n FROM backtest_runs "
                "WHERE user_id = ? AND strategy_id = ?",
                (user_id, strategy_id),
            ).fetchone()["n"]
            pnl = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0.0) AS pnl FROM trades "
                "WHERE user_id = ? AND strategy_id = ?",
                (user_id, strategy_id),
            ).fetchone()["pnl"]
            return {
                "strategy": strategy_row_to_dict(
                    fresh, runs_count=runs_count, realized_pnl=pnl or 0.0
                )
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @router.delete("/{strategy_id}")
    async def delete_strategy(strategy_id: str, request: Request):
        """Delete a non-live strategy. Live → 400; unknown/foreign → 404.

        Trades keep their ``strategy_id`` attribution (append-only log).
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = _get_owned(conn, strategy_id, user_id)
            if row is None:
                conn.rollback()
                return JSONResponse(
                    status_code=404, content={"error": "Strategy not found"}
                )
            if row["status"] == "live":
                conn.rollback()
                return JSONResponse(
                    status_code=400,
                    content={"error": "Cannot delete a live strategy — pause or archive first"},
                )
            conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"status": "ok"}

    @router.get("/{strategy_id}/performance")
    async def strategy_performance(strategy_id: str, request: Request):
        """Realized-P&L stats + 0-baseline equity curve + fills (contract §6).

        stats: realized_pnl (Σ sell realized_pnl, 2dp), round_trips (sells
        with a realized P&L), win_rate (analytics 4dp), profit_factor
        (backtest 2dp; None when loss-free), max_drawdown_pct (the shared
        ``_max_drawdown_pct`` over the curve values; 0.0 when the curve is
        too short), fires (executed strategy buys). equity_curve: cumulative
        realized P&L at each sell + a final mark-to-market point while a
        position is open.
        """
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            row = _get_owned(conn, strategy_id, user_id)
            if row is None:
                return JSONResponse(
                    status_code=404, content={"error": "Strategy not found"}
                )
            trade_rows = conn.execute(
                """
                SELECT id, ticker, side, quantity, price, commission,
                       realized_pnl, executed_at
                FROM trades
                WHERE user_id = ? AND strategy_id = ?
                ORDER BY executed_at ASC, rowid ASC
                """,
                (user_id, strategy_id),
            ).fetchall()
        finally:
            conn.close()

        trades = [
            {
                "id": t["id"],
                "ticker": t["ticker"],
                "side": t["side"],
                "quantity": t["quantity"],
                "price": t["price"],
                "commission": t["commission"],
                "realized_pnl": t["realized_pnl"],
                "executed_at": t["executed_at"],
            }
            for t in trade_rows
        ]

        sell_pnls = [
            t["realized_pnl"]
            for t in trade_rows
            if t["side"] == "sell" and t["realized_pnl"] is not None
        ]
        fires = sum(1 for t in trade_rows if t["side"] == "buy")
        round_trips = len(sell_pnls)
        wins = [p for p in sell_pnls if p > 0]
        losses = [p for p in sell_pnls if p < 0]
        gross_losses = -sum(losses)
        realized_pnl = round(sum(sell_pnls), 2)
        win_rate = round(len(wins) / round_trips, 4) if round_trips else None
        profit_factor = (
            round(sum(wins) / gross_losses, 2) if gross_losses > 0 else None
        )

        # 0-baseline P&L curve: cumulative realized P&L at each sell time,
        # plus a live mark-to-market point while a position is open.
        equity_curve: list[dict] = []
        cumulative = 0.0
        for t in trade_rows:
            if t["side"] == "sell" and t["realized_pnl"] is not None:
                cumulative += t["realized_pnl"]
                equity_curve.append(
                    {
                        "time": _iso_to_epoch(t["executed_at"]),
                        "value": round(cumulative, 2),
                    }
                )
        if row["open_qty"] and row["open_qty"] > 0 and row["open_price"]:
            quote = price_cache.get(row["ticker"])
            if quote is not None:
                unrealized = (quote.price - row["open_price"]) * row["open_qty"]
                equity_curve.append(
                    {
                        "time": int(datetime.now(timezone.utc).timestamp()),
                        "value": round(cumulative + unrealized, 2),
                    }
                )

        max_dd = _max_drawdown_pct([p["value"] for p in equity_curve])
        return {
            "stats": {
                "realized_pnl": realized_pnl,
                "round_trips": round_trips,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "max_drawdown_pct": max_dd if max_dd is not None else 0.0,
                "fires": fires,
            },
            "equity_curve": equity_curve,
            "trades": trades,
        }

    return router
