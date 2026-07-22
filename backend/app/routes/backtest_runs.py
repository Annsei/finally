"""Run Library API — persisted backtest runs (P2 §5).

Provides:
- POST   /api/backtest/runs      — run + persist a backtest. Body is one of:
  ``{strategy_id, days?, runs?, seed?, label?}`` (config built from the
  strategy row via ``normalize_strategy_backtest_config``; unknown/foreign
  strategy → 404) or the legacy Backtest-tab field set ``{ticker,
  trigger_type, threshold, quantity, side?, take_profit_pct?, stop_loss_pct?,
  days?, runs?, seed?, label?}``. Legacy saves are RE-RUN server side with
  the same config+seed — the client never submits stats, so persisted
  numbers cannot be forged (end_time is "now"; the seed makes the math
  trustworthy even though bar timestamps differ from the tab's render).
  → 201 ``{"run": {...}}``; validation failures → 400 ``{"error": msg}``.
- GET    /api/backtest/runs      — list (newest first, NO curves): optional
  ``strategy_id`` / ``ticker`` filters, ``limit`` default 50 clamped 1..200.
- GET    /api/backtest/runs/{id} — full payload; unknown/foreign → 404.
- DELETE /api/backtest/runs/{id} — ``{"status": "ok"}``; unknown/foreign → 404.

Storage: config/stats/equity_curve/baseline_curve/trades/runs_summary are the
engine response blocks stored as JSON text. Curves arrive already ≤400-point
downsampled from the engine; the trade log is truncated to the first 200
entries AT WRITE TIME (contract §1).

``insert_backtest_run_on_conn`` is shared with the chat 'backtest' strategy
action (§7) — it does NOT commit (the chat turn owns its transaction).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, FiniteFloat

from app.auth import get_current_user_id
from app.backtest import (
    STARTING_CASH,
    attach_history_bars,
    normalize_backtest_config,
    normalize_strategy_backtest_config,
    run_backtest,
)
from app.db.connection import get_conn
from app.market.cache import PriceCache
from app.market.profiles import MarketProfile

logger = logging.getLogger(__name__)

MAX_STORED_TRADES = 200
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

_RUN_SELECT_COLUMNS = (
    "id, strategy_id, label, created_at, config, stats, equity_curve, "
    "baseline_curve, trades, runs_summary"
)


class SaveRunRequest(BaseModel):
    """POST body — strategy shape (strategy_id) or the legacy field set."""

    strategy_id: str | None = None
    label: str | None = None
    days: int | None = None
    runs: int | None = None
    seed: int | None = None
    source: str | None = None  # D1 §3: "synthetic" (default) | "history"
    # Legacy Backtest-tab fields (used when strategy_id is absent):
    ticker: str | None = None
    trigger_type: str | None = None
    threshold: FiniteFloat | None = None
    quantity: FiniteFloat | None = None
    side: str | None = None
    take_profit_pct: FiniteFloat | None = None
    stop_loss_pct: FiniteFloat | None = None


def insert_backtest_run_on_conn(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    strategy_id: str | None,
    label: str | None,
    result: dict,
) -> dict:
    """Persist one engine result to ``backtest_runs`` WITHOUT committing.

    ``result`` is a full ``run_backtest`` response. The stored trade log is
    truncated to the first ``MAX_STORED_TRADES`` entries (contract §1).
    Returns the full public run payload (the POST 201 / GET {id} shape).
    """
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    trades = result["trades"][:MAX_STORED_TRADES]
    conn.execute(
        """
        INSERT INTO backtest_runs (id, user_id, strategy_id, label, created_at,
            config, stats, equity_curve, baseline_curve, trades, runs_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            user_id,
            strategy_id,
            label,
            created_at,
            json.dumps(result["config"]),
            json.dumps(result["stats"]),
            json.dumps(result["equity_curve"]),
            json.dumps(result["baseline_curve"]),
            json.dumps(trades),
            json.dumps(result["runs_summary"])
            if result["runs_summary"] is not None
            else None,
        ),
    )
    return {
        "id": run_id,
        "strategy_id": strategy_id,
        "label": label,
        "created_at": created_at,
        "config": result["config"],
        "stats": result["stats"],
        "equity_curve": result["equity_curve"],
        "baseline_curve": result["baseline_curve"],
        "trades": trades,
        "runs_summary": result["runs_summary"],
    }


def _row_to_full_run(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "label": row["label"],
        "created_at": row["created_at"],
        "config": json.loads(row["config"]),
        "stats": json.loads(row["stats"]),
        "equity_curve": json.loads(row["equity_curve"]),
        "baseline_curve": json.loads(row["baseline_curve"]),
        "trades": json.loads(row["trades"]),
        "runs_summary": json.loads(row["runs_summary"])
        if row["runs_summary"] is not None
        else None,
    }


def _row_to_list_item(row: sqlite3.Row) -> dict:
    """List-shape item: identity + config scalars + stats — NO curves (§5)."""
    config = json.loads(row["config"])
    item = {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "label": row["label"],
        "created_at": row["created_at"],
        "ticker": config.get("ticker"),
        "days": config.get("days"),
        "runs": config.get("runs"),
        "seed": config.get("seed"),
        "stats": json.loads(row["stats"]),
    }
    # D1 §5: history runs surface their DATA source on the list row (the
    # Run Library badge). Synthetic runs stay shape-identical — no key for
    # legacy configs, and the strategy shape's "strategy" marker is not a
    # data source.
    source = config.get("source")
    if source is not None and source != "strategy":
        item["source"] = source
    return item


def create_backtest_runs_router(
    price_cache: PriceCache,
    db_path: str,
    commission_bps: float = 0.0,
    profile: MarketProfile | None = None,
) -> APIRouter:
    """Factory: build the Run Library APIRouter with injected dependencies.

    Args:
        price_cache: Shared live price cache — anchor-price source for the
            server-side re-runs (seed-price fallback inside the normalizers).
        db_path: Path to the SQLite database file.
        commission_bps: Commission applied to simulated fills — the same
            startup value every other execution path uses.
        profile: Active market profile (CN) — universe anchors/params, seed
            cash, and the A-share fee/T+1 engine semantics. None keeps the
            US constants and $10,000.
    """
    universe = profile.universe if profile is not None else None
    starting_cash = profile.seed_cash if profile is not None else STARTING_CASH
    market = profile.key if profile is not None else "us"
    router = APIRouter(prefix="/api/backtest/runs", tags=["backtest-runs"])

    @router.post("", status_code=201)
    async def save_run(body: SaveRunRequest, request: Request):
        """Run a backtest server-side and persist it (contract §5)."""
        user_id = get_current_user_id(request, db_path)
        strategy_id: str | None = None

        if body.strategy_id is not None:
            conn = get_conn(db_path)
            try:
                row = conn.execute(
                    "SELECT id, ticker, entry, exits, sizing FROM strategies "
                    "WHERE id = ? AND user_id = ?",
                    (body.strategy_id, user_id),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return JSONResponse(
                    status_code=404, content={"error": "Strategy not found"}
                )
            strategy_id = row["id"]
            outcome = normalize_strategy_backtest_config(
                price_cache,
                strategy_row=row,
                days=body.days,
                runs=body.runs,
                seed=body.seed,
                universe=universe,
                profile=profile,
                source=body.source,
            )
        else:
            missing = [
                name
                for name, value in (
                    ("ticker", body.ticker),
                    ("trigger_type", body.trigger_type),
                    ("threshold", body.threshold),
                    ("quantity", body.quantity),
                )
                if value is None
            ]
            if missing:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Provide a strategy_id or the legacy backtest "
                        f"fields (missing: {', '.join(missing)})"
                    },
                )
            outcome = normalize_backtest_config(
                price_cache,
                ticker=body.ticker,
                trigger_type=body.trigger_type,
                threshold=body.threshold,
                quantity=body.quantity,
                side=body.side,
                take_profit_pct=body.take_profit_pct,
                stop_loss_pct=body.stop_loss_pct,
                days=body.days,
                runs=body.runs,
                seed=body.seed,
                universe=universe,
                profile=profile,
                source=body.source,
            )

        if outcome["status"] == "failed":
            return JSONResponse(status_code=400, content={"error": outcome["error"]})
        config = outcome["config"]

        # D1 §3: history mode loads the stored daily-bar window first.
        if config.get("data_source") == "history":
            conn = get_conn(db_path)
            try:
                error = attach_history_bars(config, conn, market=market)
            finally:
                conn.close()
            if error is not None:
                return JSONResponse(status_code=400, content={"error": error})

        # Server-side (re-)run — the client never supplies stats (§5).
        result = await asyncio.to_thread(
            run_backtest,
            config,
            commission_bps=commission_bps,
            end_time=time.time(),
            starting_cash=starting_cash,
            profile=profile,
        )

        label = body.label.strip() if body.label and body.label.strip() else None
        conn = get_conn(db_path)
        try:
            run = insert_backtest_run_on_conn(
                conn,
                user_id=user_id,
                strategy_id=strategy_id,
                label=label,
                result=result,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"run": run}

    @router.get("")
    async def list_runs(
        request: Request,
        strategy_id: str | None = None,
        ticker: str | None = None,
        limit: str | None = None,
    ) -> dict:
        """List runs newest-first — stats only, never curves (contract §5).

        Query params:
            strategy_id: exact-match filter.
            ticker: uppercase-normalized exact match on the config's ticker.
            limit: default 50, clamped 1..200; non-integer values → 400.
        """
        if limit is None:
            limit_value = DEFAULT_LIST_LIMIT
        else:
            try:
                limit_value = int(limit)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": "limit must be an integer"}
                )
        limit_value = max(1, min(MAX_LIST_LIMIT, limit_value))
        ticker_value = (
            ticker.strip().upper() if ticker is not None and ticker.strip() else None
        )

        user_id = get_current_user_id(request, db_path)
        query = (
            "SELECT id, strategy_id, label, created_at, config, stats "
            "FROM backtest_runs WHERE user_id = ?"
        )
        params: list = [user_id]
        if strategy_id is not None:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        query += " ORDER BY created_at DESC, rowid DESC"

        conn = get_conn(db_path)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        items = []
        for row in rows:
            item = _row_to_list_item(row)
            if ticker_value is not None and item["ticker"] != ticker_value:
                continue
            items.append(item)
            if len(items) >= limit_value:
                break
        return {"runs": items}

    @router.get("/{run_id}")
    async def get_run(run_id: str, request: Request):
        """Full run payload (curves + trades). Unknown/foreign ids → 404."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                f"SELECT {_RUN_SELECT_COLUMNS} FROM backtest_runs "
                "WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return JSONResponse(status_code=404, content={"error": "Run not found"})
        return {"run": _row_to_full_run(row)}

    @router.delete("/{run_id}")
    async def delete_run(run_id: str, request: Request):
        """Delete one run. Unknown/foreign ids → 404."""
        user_id = get_current_user_id(request, db_path)
        conn = get_conn(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM backtest_runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return JSONResponse(
                    status_code=404, content={"error": "Run not found"}
                )
            conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"status": "ok"}

    return router
